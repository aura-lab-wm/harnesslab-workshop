"""Grade the workspace the agent leaves behind.

Three test tiers per task:
  visible   tests/            the agent can see and run these
  hidden    hidden_tests/     the benchmark's oracle (what "resolved" means)
  strong    hidden_tests + hidden_tests_strong/   a strengthened oracle (UTBoost-style, a superset
                                    of hidden); used in exercise 5 to show how weak tests inflate scores

Grading always runs on a *fresh copy* of the workspace with the original test
directory restored, so an agent that edits tests cannot make hidden tests pass
by editing them (we still record that it tried: see `tests_modified`).
"""
from __future__ import annotations
import json, os, shutil, subprocess, tempfile

from .tools import subprocess_env


def _run_unittest(workdir: str, tests_dir: str = "tests", timeout: int = 120) -> tuple[bool, str]:
    cmd = ["python", "-m", "unittest", "discover", "-s", tests_dir, "-q"]
    try:
        r = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=timeout,
                           env={**subprocess_env(), "PYTHONDONTWRITEBYTECODE": "1"})
        out = (r.stdout + r.stderr)[-4000:]
        return r.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, "grader timeout"


def grade(task_dir: str, workdir: str) -> dict:
    """Return {'visible': bool, 'hidden': bool, 'strong': bool, 'logs': {...}}."""
    result = {"logs": {}}
    # the strengthened tier is a superset of the hidden tier (UTBoost-style augmentation adds tests, it does not replace them)
    for tier, subdirs in (("visible", ()), ("hidden", ("hidden_tests",)), ("strong", ("hidden_tests", "hidden_tests_strong"))):
        tmp = tempfile.mkdtemp(prefix="grade_")
        try:
            shutil.copytree(workdir, tmp, dirs_exist_ok=True)
            # restore pristine visible tests (defeats test tampering), then add tier tests
            tests_dst = os.path.join(tmp, "tests")
            shutil.rmtree(tests_dst, ignore_errors=True)
            shutil.copytree(os.path.join(task_dir, "repo", "tests"), tests_dst)
            for subdir in subdirs:
                for f in os.listdir(os.path.join(task_dir, subdir)):
                    if f.endswith(".py"):
                        shutil.copy(os.path.join(task_dir, subdir, f), tests_dst)
            ok, log = _run_unittest(tmp)
            result[tier] = ok
            result["logs"][tier] = log
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return result


def load_task(task_dir: str) -> dict:
    with open(os.path.join(task_dir, "task.json")) as f:
        meta = json.load(f)
    with open(os.path.join(task_dir, "issue.md")) as f:
        meta["issue"] = f.read()
    meta["dir"] = task_dir
    return meta
