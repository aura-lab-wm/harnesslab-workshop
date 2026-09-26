"""Where the lab lives.

The platform reads harness configs, tasks and run data from a *lab root*. In a git checkout
that root is the repository itself; from an installed wheel there is no checkout, so the root
becomes a workspace directory that is seeded once from the data bundled inside the wheel.

Resolution order (first hit wins):

  1. ``$HARNESSLAB_LAB``                        explicit override (also set by ``harnesslab --lab``)
  2. a checkout: ``<this file>/../../``        if that directory contains ``harnesses/``
  3. ``~/.harnesslab``                          workspace, seeded on first use from the bundle

Everything else in the package should import ``LAB_ROOT`` (or the derived directories) from here
instead of doing path arithmetic on ``__file__``, so that one rule covers checkout and wheel.

Because ``LAB_ROOT`` is resolved at import time, an override has to be in the environment before
``harnesslab.backend.app`` is imported -- which is exactly what ``harnesslab.__main__.main`` does.
"""
from __future__ import annotations

import os
import shutil

__all__ = [
    "LAB_ROOT", "RUNS_ROOT", "HARNESS_DIR", "TASK_DIR", "DATA_ROOT", "DIST", "MODELS_DIR",
    "INDEX_DB", "BUNDLE_DIR", "resolve_lab_root", "ensure_workspace", "bundle_dir", "describe",
]

from .labroot import (  # noqa: F401  (re-exported: the pure resolver lives in labroot)
    _BACKEND_DIR, _CHECKOUT_GUESS, _PKG_DIR, DEFAULT_WORKSPACE, SEEDED,
    _is_checkout, bundle_dir, ensure_workspace, resolve_lab_root,
)


LAB_ROOT = resolve_lab_root()

DATA_ROOT = os.path.join(LAB_ROOT, "data")
RUNS_ROOT = os.path.join(DATA_ROOT, "runs")
HARNESS_DIR = os.path.join(LAB_ROOT, "harnesses")
TASK_DIR = os.path.join(LAB_ROOT, "tasks")
INDEX_DB = os.path.join(DATA_ROOT, ".harnesslab_index.sqlite")

#: The built UI. It always ships with the package, never with the lab root.
DIST = os.path.join(_PKG_DIR, "frontend", "dist")
MODELS_DIR = os.path.join(_PKG_DIR, "data", "models")
BUNDLE_DIR = bundle_dir()


def describe() -> dict:
    """One dict for ``harnesslab --help``-style diagnostics and the packaging tests."""
    return {
        "lab_root": LAB_ROOT,
        "source": ("env HARNESSLAB_LAB" if os.environ.get("HARNESSLAB_LAB") or os.environ.get("HOLDSTILL_LAB") else
                   "checkout" if _is_checkout(_CHECKOUT_GUESS) else "workspace"),
        "runs_root": RUNS_ROOT, "harnesses": HARNESS_DIR, "tasks": TASK_DIR,
        "dist": DIST, "dist_built": os.path.isdir(DIST), "bundle": BUNDLE_DIR, "index_db": INDEX_DB,
    }
