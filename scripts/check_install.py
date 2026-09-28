"""Check that HarnessLab works on this machine: one command, about ten seconds, offline, free.

    python scripts/check_install.py          # or: make check

It runs five checks and says what to do if one fails:

  1. the package imports and reports its version
  2. the web interface is built
  3. the recorded datasets are present
  4. the mock agent runs and the grader grades it (expect exactly 14 of 24 PASS)
  5. an exercise script reads those runs

Nothing is written under data/runs/: the mock sweep goes to a temporary folder that is deleted
afterwards. No API key and no network are needed.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_VERSION = "0.3.0"
EXPECTED_PASS, EXPECTED_RUNS = 14, 24

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> bool:
    results.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}: {detail}", flush=True)
    return ok


def run(*args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout)


def main() -> int:
    print(f"HarnessLab install check  (python {sys.version.split()[0]}, {ROOT})\n")

    # 1. version
    p = run("-m", "harnesslab", "--version")
    out = (p.stdout + p.stderr).strip()
    check("version", p.returncode == 0 and EXPECTED_VERSION in out,
          out or "no output" if p.returncode == 0 else
          "harnesslab does not import: activate your .venv, then run `python -m pip install -e .`")

    # 2. web interface
    p = run("-m", "harnesslab", "--paths")
    try:
        paths = json.loads(p.stdout)
        built = bool(paths.get("dist_built"))
    except (ValueError, TypeError):
        paths, built = {}, False
    check("web interface", built,
          "built" if built else "not built: unzip/clone again, or `cd harnesslab/frontend && npm ci && npm run build`")

    # 3. recorded data
    idx = ROOT / "data" / "runs" / "llma4se_live" / "index.jsonl"
    n = sum(1 for line in idx.open() if line.strip()) if idx.exists() else 0
    check("recorded data", n > 0, f"llma4se_live has {n:,} runs" if n else "data/runs/llma4se_live is missing")

    # 4 + 5. mock sweep, then an exercise on it
    with tempfile.TemporaryDirectory(prefix="harnesslab-check-") as tmp:
        out_dir = Path(tmp) / "check"
        p = run("-m", "harnesslab", "run", "--provider", "mock", "--tasks", "all", "--repeats", "3",
                "--price", "0,0", "--out", str(out_dir))
        index = out_dir / "index.jsonl"
        rows = [json.loads(l) for l in index.open() if l.strip()] if index.exists() else []
        passed = sum(1 for r in rows if r.get("hidden_pass") is True)
        if p.returncode != 0 or not rows:
            check("mock agent + grader", False, "the runner failed:\n" + (p.stderr or p.stdout)[-800:])
        elif (passed, len(rows)) == (EXPECTED_PASS, EXPECTED_RUNS):
            check("mock agent + grader", True, f"{passed} / {len(rows)} PASS, as expected")
        else:
            check("mock agent + grader", False,
                  f"{passed} / {len(rows)} PASS, expected {EXPECTED_PASS} / {EXPECTED_RUNS}. The grader, not the "
                  "model, is broken: usually a pyenv `python` shim that cannot run the task tests. "
                  "See docs/STUDENT_GUIDE.md, section 13.")

        if rows:
            p = run("exercises/ex1_variance.py", "--results", str(out_dir))
            check("exercise script", p.returncode == 0 and "pass@1" in p.stdout,
                  "ex1_variance.py reads the runs" if p.returncode == 0 else "ex1_variance.py failed:\n" + p.stderr[-800:])

    failed = [r for r in results if not r[1]]
    print()
    if failed:
        print(f"{len(failed)} of {len(results)} checks failed. Send this output to your instructor.")
        return 1
    print(f"All {len(results)} checks passed. Start the workbench with `python -m harnesslab` "
          "(in Codespaces it is already running on port 8765).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
