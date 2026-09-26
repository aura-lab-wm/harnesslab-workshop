"""Resolving the lab root, with no side effects at import.

paths.py resolves LAB_ROOT the moment it is imported, seeding a workspace if the root is not a
checkout. That is right for the app, which is about to use the lab, and wrong for anything that only
needs to ASK where the lab is -- importing paths just to call resolve_lab_root(seed=False) had
already seeded one. This module holds the pure resolver so a caller can ask without creating
anything; paths.py imports it from here and keeps its behaviour.
"""
from __future__ import annotations

import os
import shutil

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))          # .../harnesslab/backend
_PKG_DIR = os.path.dirname(_BACKEND_DIR)                            # .../harnesslab
_CHECKOUT_GUESS = os.path.dirname(_PKG_DIR)                         # .../lab  (only in a checkout)

#: Directories seeded into a fresh workspace, and shipped in the wheel under ``harnesslab/bundle/``.
SEEDED = ("harnesses", "tasks")

DEFAULT_WORKSPACE = os.path.join(os.path.expanduser("~"), ".harnesslab")


def bundle_dir() -> str | None:
    """Directory holding the packaged copies of ``harnesses/`` and ``tasks/``.

    Present only in an installed wheel (``harnesslab/bundle/``); ``None`` in a plain checkout,
    where the checkout itself already holds the originals.
    """
    b = os.path.join(_PKG_DIR, "bundle")
    return b if os.path.isdir(os.path.join(b, "harnesses")) else None


def _is_checkout(path: str) -> bool:
    return os.path.isdir(os.path.join(path, "harnesses")) and os.path.isdir(os.path.join(path, "tasks"))


def ensure_workspace(root: str) -> str:
    """Create ``root`` if needed and seed it with the packaged harnesses/tasks (never overwrites).

    Always leaves ``root/data/runs`` in place so live runs have somewhere to land.
    """
    os.makedirs(os.path.join(root, "data", "runs"), exist_ok=True)
    src = bundle_dir()
    if src:
        for name in SEEDED:
            dst = os.path.join(root, name)
            if not os.path.exists(dst) and os.path.isdir(os.path.join(src, name)):
                shutil.copytree(os.path.join(src, name), dst)
    else:
        for name in SEEDED:
            os.makedirs(os.path.join(root, name), exist_ok=True)
    return root


def resolve_lab_root(env: dict | None = None, start: str | None = None, workspace: str | None = None,
                     seed: bool = True) -> str:
    """Pure resolver -- see the module docstring. Exposed separately so it can be unit tested.

    ``env`` defaults to ``os.environ``, ``start`` to the checkout guess derived from ``__file__``,
    ``workspace`` to ``~/.harnesslab``. With ``seed=False`` nothing is written to disk.
    """
    env = os.environ if env is None else env
    start = _CHECKOUT_GUESS if start is None else start
    workspace = DEFAULT_WORKSPACE if workspace is None else workspace

    override = (env.get("HARNESSLAB_LAB") or env.get("SINGLETREE_LAB") or env.get("ARGUS_LAB")
                or env.get("HOLDSTILL_LAB") or "").strip()   # legacy names still honoured
    if override:
        root = os.path.abspath(os.path.expanduser(override))
        if seed and not _is_checkout(root):
            ensure_workspace(root)
        return root

    if _is_checkout(start):
        return os.path.abspath(start)

    root = os.path.abspath(os.path.expanduser(workspace))
    if seed:
        ensure_workspace(root)
    return root
