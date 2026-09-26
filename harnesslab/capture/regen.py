"""Rebuild the captured runs of one session file, whole, and swap them in atomically.

Never incremental: the importer's conversion is not prefix-stable (spec §5.1), and a whole-file
rebuild of an 11 MB session measured 0.11 s. Each segment is written into `<run_id>.tmp-<pid>/` and
then swapped into place, so a reader sees the old run, briefly no run, then the new run -- never a
mix of old and new files.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field

from harnesslab.backend.importers import common as C

from . import identity
from .adapters import BY_NAME, missing
from .harness_config import capture_harness
from .writer import write_run

#: adapter name -> the module that parses it. One source of truth: capture.adapters.REGISTRY.
ADAPTERS = {name: ad.module for name, ad in BY_NAME.items()}


@dataclass
class Regenerated:
    run_id: str
    summary: object
    extras: dict
    uuids: list = field(default_factory=list)
    last_ts: str = ""
    source_path: str = ""


def _swap_in(tmp_dir: str, final_dir: str) -> None:
    old = f"{final_dir}.old-{os.getpid()}"
    # Clear it FIRST. The pid is in the name, so a directory left by a process that died mid-swap is
    # hit again by every later process that recycles that pid -- os.replace onto a non-empty
    # destination raises, and the run can never be regenerated until someone removes it by hand.
    shutil.rmtree(old, ignore_errors=True)
    if os.path.isdir(final_dir):
        os.replace(final_dir, old)
    os.replace(tmp_dir, final_dir)
    shutil.rmtree(old, ignore_errors=True)


def _segments(source_path: str, gap_s: float, adapter: str):
    """(session, segment, run_id, previous run_id) for every segment a source yields, in order.

    The one place run ids are derived. regenerate() writes them; run_ids() only names them, so
    whoever asks which runs a source produces NOW gets the answer a capture would write.
    """
    mod = ADAPTERS[adapter]
    for sess in mod.sessions(source_path):
        sess.events = identity.dedup_events(sess.events)
        segments = [s for s in identity.split_session(sess, gap_s) if s.events]
        if not segments:
            continue
        base_id = C.run_id_for(sess, C.derive_task_id(segments[0]))
        previous = ""
        for seg in segments:
            n = seg.extra["segment"]
            run_id = base_id if n == 0 else f"{base_id}-s{n}"
            yield sess, seg, run_id, previous

            previous = run_id


def run_ids(source_path: str, gap_s: float, adapter: str = "claude_code") -> list[str]:
    """The run ids a capture of this source would write, reading it and writing nothing."""
    return [run_id for _, _, run_id, _ in _segments(source_path, gap_s, adapter)]


def regenerate(source_path: str, out_root: str, gap_s: float, adapter: str = "claude_code") -> list[Regenerated]:
    # what this source cannot record becomes unknown in the ledger, never zero
    gaps = missing(BY_NAME[adapter])
    produced: list[Regenerated] = []
    for sess, seg, run_id, previous in _segments(source_path, gap_s, adapter):
        n = seg.extra["segment"]
        task_id = C.derive_task_id(seg)
        uuids = identity.conv_uuids(seg.events)
        start = {"root_uuid": uuids[0] if uuids else "", "last_uuid": uuids[-1] if uuids else "",
                 "segment": n, "resumed_from": previous if n else "",
                 "parent_session_id": sess.extra.get("parent_session_id", ""),
                 "project": os.path.basename(os.path.normpath(seg.cwd)) if seg.cwd else ""}
        final_dir = os.path.join(out_root, run_id)
        tmp_dir = f"{final_dir}.tmp-{os.getpid()}"
        shutil.rmtree(tmp_dir, ignore_errors=True)
        summary, extras = write_run(seg, tmp_dir, run_id, task_id, capture_harness(seg), extra_start=start,
                                    missing=gaps)
        _swap_in(tmp_dir, final_dir)
        produced.append(Regenerated(run_id=run_id, summary=summary, extras=extras, uuids=uuids,
                                    last_ts=next((e.ts for e in reversed(seg.events) if e.ts), ""),
                                    source_path=source_path))
    return produced
