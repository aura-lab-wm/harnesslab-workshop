"""The capture allow-list: which directories the spine may read.

Stored in ~/.harnesslab/capture.json as {"paths": [...]}. The console's Capture page is its only
writer (spec §9.1, Plan 2); this module only reads it. Paths are compared after `realpath`, so a
symlink or a worktree path cannot point the spine at a directory that was never added. A missing,
corrupt or malformed config allows nothing.
"""
from __future__ import annotations

import json
import os


def config_path() -> str:
    return (os.environ.get("HARNESSLAB_CAPTURE_CONFIG")
            or os.path.join(os.path.expanduser("~"), ".harnesslab", "capture.json"))


def load_paths(path: str | None = None) -> list[str]:
    p = path or config_path()
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    raw = data.get("paths") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    return [os.path.realpath(os.path.expanduser(x)) for x in raw if isinstance(x, str) and x.strip()]


def is_allowed(candidate: str, roots: list[str]) -> bool:
    real = os.path.realpath(candidate)
    for r in roots:
        base = r.rstrip(os.sep) or os.sep
        if real == base or real.startswith(base.rstrip(os.sep) + os.sep):
            return True
    return False
