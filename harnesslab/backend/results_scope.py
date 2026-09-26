"""The one place that decides which results directories a listing may show.

Every endpoint and exporter that enumerates data/runs/ goes through `list_results_dirs`.
Captured runs (data/runs/captured/) hold a person's real agent sessions, and their task ids
embed a slug of the first prompt, so they are excluded from every default listing and appear
only when a caller names them explicitly. tests_agentlab/test_capture_scope.py fails if a module
lists data/runs/ without coming through here.
"""
from __future__ import annotations

import os
from typing import Callable

PRIVATE_DIRS = frozenset({"captured"})


def is_private(runs_root: str, name: str) -> bool:
    """True when `name` refers to a private results directory.

    Decided by what the name refers to, not how it is spelled: on a case-insensitive filesystem
    (the macOS default) 'Captured', 'captured/' and './captured' all open the private directory,
    and a symlink can alias it under another name. Anything we cannot resolve fails closed.
    """
    cleaned = (name or "").strip()
    if os.path.normpath(cleaned).casefold() in {p.casefold() for p in PRIVATE_DIRS}:
        return True
    target = os.path.join(runs_root, cleaned)
    for p in PRIVATE_DIRS:
        private = os.path.join(runs_root, p)
        try:
            if os.path.exists(target) and os.path.exists(private) and os.path.samefile(target, private):
                return True
        except OSError:
            return True
    return False


def list_results_dirs(runs_root: str, include_private: bool = False) -> list[str]:
    """Names of results directories under `runs_root` that have an index.jsonl, sorted."""
    if not os.path.isdir(runs_root):
        return []
    return sorted(
        name for name in os.listdir(runs_root)
        if (include_private or not is_private(runs_root, name))
        and os.path.exists(os.path.join(runs_root, name, "index.jsonl")))


def count_runs(runs_root: str, name: str) -> int:
    idx = os.path.join(runs_root, name, "index.jsonl")
    if not os.path.exists(idx):
        return 0
    with open(idx, encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def confirm_private_export(picked: list[str], runs_root: str, assume_yes: bool, isatty: bool,
                           ask: Callable[[str], str]) -> bool:
    """True when an export may proceed. Naming a private directory needs an explicit yes:
    `assume_yes` in a script, or a typed 'yes' at an interactive prompt that states the run count.
    A non-interactive caller without `assume_yes` is refused, so a pipeline can never ship
    captured sessions by accident."""
    private = [d for d in picked if is_private(runs_root, d)]
    if not private:
        return True
    if assume_yes:
        return True
    if not isatty:
        return False
    n = sum(count_runs(runs_root, d) for d in private)
    answer = ask(f"This export includes {n} captured session run(s) from your real agent work "
                 f"({', '.join(private)}). Type 'yes' to include them: ")
    return answer.strip().lower() == "yes"
