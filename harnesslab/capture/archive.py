"""A compressed copy of every captured source file.

The spine's purity invariant is `ledgers = f(raw source files, adapter version)`, and Claude Code
deletes session files after 30 days by default. A ledger is lossy (about 1.2% of source size, with
truncated previews), so once a source is pruned a fixed adapter could never regenerate its run.
This keeps the sources. Stdlib gzip; one archive per source path, rewritten only when the source
has changed size or modification time. The directory is private (0700): it holds real sessions.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil


def default_root() -> str:
    return (os.environ.get("HARNESSLAB_CAPTURE_ARCHIVE")
            or os.path.join(os.path.expanduser("~"), ".harnesslab", "raw-archive"))


def archive_source(path: str, archive_root: str) -> bool:
    """Archive `path` if it is new or changed since its last archive. Returns True if written."""
    st = os.stat(path)
    real = os.path.realpath(path)
    key = hashlib.sha256(real.encode("utf-8")).hexdigest()[:16]
    dest = os.path.join(archive_root, f"{key}-{os.path.basename(path)}.gz")
    meta_path = dest + ".meta.json"
    meta = {"source": real, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
    if os.path.exists(dest) and os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            if json.load(f) == meta:
                return False
    os.makedirs(archive_root, mode=0o700, exist_ok=True)
    os.chmod(archive_root, 0o700)
    tmp = f"{dest}.tmp-{os.getpid()}"
    with open(path, "rb") as src, gzip.open(tmp, "wb", compresslevel=6) as out:
        shutil.copyfileobj(src, out, 1 << 20)
        archived_bytes = src.tell()
    os.replace(tmp, dest)
    meta["size"] = archived_bytes            # what was actually copied, not the pre-copy stat --
                                               # the source may have grown while being read
    with open(meta_path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    os.replace(meta_path + ".tmp", meta_path)
    return True
