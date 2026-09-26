"""Data for the Capture page, and the one thing it is allowed to write.

The private guard hides any request whose PATH names a private results directory. These routes do
not name one -- `/api/capture/status` says nothing about `captured` -- so the guard cannot see them,
and a route here that answered an unmarked request would hand the operator's real sessions to any
page on the machine. The opt-in is therefore declared on the ROUTER, not on each route, so a route
added later is covered without anyone remembering to cover it. That same header is what keeps the
write below off the reach of another page on this machine: a custom header cannot be sent
cross-origin without a CORS preflight, and this app grants none.

Everything here reads, except `POST /control`, which writes captured/control.json -- `paused` and
`nudge_at`, the two fields the sniffer already reads between passes -- and nothing else.

That exception is as far as it goes. Triggering a CAPTURE from the UI would take CaptureLock for the
length of a pass -- minutes on a corpus this size -- and rewrite inflight.json and identity.json
wholesale from that pass, which is a race with any sniffer and a discussion this page does not need
to have; removing anything (a sweep's --apply, a prune) decides against the operator what is debris,
from a page that cannot show them the list first. Both stay commands typed at a terminal. A pause is
neither: two fields, written atomically, acted on by the watcher when it chooses, and undone by
writing them back. Without it a watcher paused from the menubar app -- an app this machine may not
even have -- can be seen on the page and not restarted from it.
"""
from __future__ import annotations

import json
import os
import time
from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .private_guard import OPT_IN_HEADER

#: Everything the console may ask the watcher for. A closed vocabulary, checked before anything is
#: written: control.json has two fields and must never grow a third off the wire, and an action this
#: route does not know is a request to do something it is not allowed to do.
CONTROL_ACTIONS = ("pause", "resume", "nudge")


class ControlIn(BaseModel):
    action: str


def _captured(root: str) -> str:
    return os.path.join(root, "captured")


def _index_rows(path: str) -> list:
    """Every parseable row. A torn last line is skipped, never raised: an interrupted capture must
    not take the page down with it."""
    rows: list = []
    if not os.path.exists(path):
        return rows
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return rows
    return rows


def _doc(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _presence(runs_root: str) -> dict:
    """What the background watcher is doing, or never_started when none has ever run.

    Counts, states and timestamps only -- the document is built that way on purpose (see
    capture/presence.py), so this can be served without filtering.
    """
    from harnesslab.capture import presence
    doc = presence.read(runs_root)
    # The document is what the watcher LAST said. A watcher killed hard never gets to say anything
    # else, so a running state is only believed while the lock says somebody is actually there. An
    # unknown answer (a mount without locking) leaves the document alone rather than guess.
    if doc.get("state") in ("idle", "scanning", "capturing", "paused", "locked") \
            and presence.sniffer_alive(runs_root) is False:
        doc["state"] = "error"
        doc["error"] = "The watcher is not running. It exited without shutting down cleanly."
    return doc


def _adapter_names() -> list:
    from harnesslab.capture.adapters import REGISTRY      # lazy: capture imports backend.importers
    return [a.name for a in REGISTRY]


def _control(runs_root: str) -> dict:
    from harnesslab.capture import presence
    return presence.read_control(runs_root)


def _cursor_counts(runs_root: str) -> dict:
    """How many sources the watcher remembers, and how many of those it adopted without reading.

    `--seed-cursors` writes a cursor for every already-captured source so a first `--watch` does not
    read the whole corpus again, and marks each one `seeded`. Nothing reported whether that had
    happened, which made "the watcher is quiet" and "the watcher was told to skip everything"
    indistinguishable from outside.
    """
    from harnesslab.capture import cursors
    cs = cursors.load(cursors.path_for(runs_root))
    return {"sources": len(cs),
            "seeded": sum(1 for c in cs.values() if isinstance(c, dict) and c.get("seeded"))}


def capture_router(runs_root_of: Callable[[], str]) -> APIRouter:
    def require_opt_in(request: Request) -> None:
        if request.headers.get(OPT_IN_HEADER) != "1":
            raise HTTPException(status_code=404, detail="Not Found")

    r = APIRouter(prefix="/api/capture", dependencies=[Depends(require_opt_in)])

    @r.get("/status")
    def status() -> dict:
        cap = _captured(runs_root_of())
        rows = _index_rows(os.path.join(cap, "index.jsonl"))
        open_runs = (_doc(os.path.join(cap, "inflight.json"), {}) or {}).get("runs") or []
        totals = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "unmeasured_runs": 0}
        for row in rows:
            # a null cost is a run whose source records no usage (Cursor): left out of the sums, and
            # counted, so a partial sum is never presented as the corpus's whole spend
            totals["unmeasured_runs"] += int("cost_usd" in row and row["cost_usd"] is None)
            for k in ("input_tokens", "output_tokens"):
                totals[k] += int(row.get(k) or 0)
            totals["cost_usd"] += float(row.get("cost_usd") or 0.0)
        totals["cost_usd"] = round(totals["cost_usd"], 6)
        idx = os.path.join(cap, "index.jsonl")
        return {
            "runs_indexed": len(rows),
            "open_runs": len(open_runs),
            "open": open_runs,
            "totals": totals,
            "adapters": _adapter_names(),
            "last_capture_at": os.path.getmtime(idx) if os.path.exists(idx) else None,
            "sniffer": _presence(runs_root_of()),
            "cursors": _cursor_counts(runs_root_of()),
            # What the control file ASKS for, which is not what presence reports. A watcher stopped
            # while paused leaves `paused` standing here and `stopped` there, so the page can only
            # say "the next watcher starts paused" if it is given both.
            "control": _control(runs_root_of()),
        }

    @r.get("/runs")
    def runs(limit: Optional[int] = None, offset: int = 0) -> dict:
        """Most recently indexed first -- index.jsonl is written in capture order, so the tail is
        the newest. No ordering stronger than that is claimed: a row carries no capture timestamp."""
        if limit is not None and limit <= 0:
            # The parameter exists to BOUND the payload. Falling through to "no bound" on 0 or -1
            # returned the entire private index in one response.
            raise HTTPException(status_code=422, detail="limit must be positive")
        rows = _index_rows(os.path.join(_captured(runs_root_of()), "index.jsonl"))
        rows.reverse()
        total = len(rows)
        start = max(0, offset)
        window = rows[start:start + limit] if limit and limit > 0 else rows[start:]
        return {"rows": window, "total": total}

    @r.post("/control")
    def control(body: ControlIn) -> dict:
        """Pause, resume, or ask for a capture now. Writes captured/control.json, and nothing else.

        `nudge` never touches `paused` (presence.nudge is built that way): asking for a pass is not
        asking to resume, and a capture-now that quietly resumed would be a pause the operator
        cannot trust. The answer is the document as it now stands, read back rather than assumed.
        """
        if body.action not in CONTROL_ACTIONS:
            raise HTTPException(status_code=422,
                                detail=f"action must be one of {', '.join(CONTROL_ACTIONS)}")
        from harnesslab.capture import presence
        root = runs_root_of()
        if body.action == "nudge":
            presence.nudge(root, time.time())
        else:
            presence.set_paused(root, body.action == "pause")
        return presence.read_control(root)

    @r.get("/relations")
    def relations() -> dict:
        doc = _doc(os.path.join(_captured(runs_root_of()), "identity.json"), {}) or {}
        return {"relations": doc.get("relations") or {}}

    return r
