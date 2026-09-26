"""Prove a built school-package zip in a clean room: extract it, install it in a throwaway venv
with no other harnesslab on the path, start the server, and drive it like a participant would.

Run standalone: python3 scripts/verify_school_package.py [ZIP]   (default: dist/harnesslab-school.zip)
Or from build_school_package.py --verify, which builds the zip fresh first.

Every check appends one {name, ok, detail, seconds} entry to the verdict; nothing here raises past
`verify()` -- a broken check is a failed entry, not a crash, so later checks (and the server-kill in
`finally`) still run. Exit code is 0 only when every check's `ok` is true.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import venv
import zipfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Below this, something is badly wrong (a config that discovers almost nothing still "passes").
# Comfortably under the ~920 the shipped suite runs today, so trimming it over time will not
# itself trip this floor -- only a collection regression that drops most of the suite will.
MIN_SHIPPED_TESTS = 300

# The scan's exception list: a name this suite's OWN fixtures use to prove a real path gets
# redacted (never a plausible real machine username), and a doc comment's literal template.
FAKE_USER_NAMES = {"someone", "me", "x"}
SECRET_PATTERNS = {
    "openrouter key (sk-or-v1- + 32+ chars)": re.compile(r"sk-or-v1-[A-Za-z0-9]{32,}"),
    "anthropic key (sk-ant-)": re.compile(r"sk-ant-[A-Za-z0-9_-]{4,}"),
    "github token (ghp_)": re.compile(r"ghp_[A-Za-z0-9]{20,}"),
}
SCAN_SKIP_PARTS = {".venv", ".git", "__pycache__", "node_modules", ".pytest_cache", "dist"}


def free_port() -> int:
    """A real bind test, not a guess: ask the OS for a free port and release it immediately."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


@contextmanager
def _check(verdict: list, name: str):
    """Runs the block, always appending a verdict entry -- an exception becomes a failed check
    with its message as `detail`, not a crash that skips the server-kill in the caller's finally."""
    t0 = time.time()
    try:
        yield
        verdict.append({"name": name, "ok": True, "detail": "", "seconds": round(time.time() - t0, 2)})
    except Exception as e:                                              # noqa: BLE001 - reported, not swallowed
        verdict.append({"name": name, "ok": False, "detail": str(e), "seconds": round(time.time() - t0, 2)})


def _run(args, **kw):
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("timeout", 300)
    return subprocess.run(args, **kw)


# What build_school_package.scrub_machine_traces rewrites in data/runs: if either survives into the
# extracted tree, the scrub was skipped or missed a shape.
MACHINE_TRACE_RE = re.compile(r"/var/folders/[A-Za-z0-9_]"
                              r"|[-dlcbps][-rwxsStT@+.]{9,11}\s+\d+\s+(?!user\s|root\s)[A-Za-z0-9_.-]+\s+(?:staff|wheel|admin|everyone)\b")


def _is_fake_users_path(text: str, start: int) -> bool:
    """`text[start:]` begins with '/Users/'. True for a placeholder this suite's own fixtures use
    to prove a real path gets redacted ('/Users/someone/...', '/Users/x/...'), a doc comment's
    literal '/Users/<name>/...' template, or the bare literal "/Users/" itself -- e.g.
    `assertNotIn("/Users/", out)`, or this very scanner's own source quoting the pattern it looks
    for -- which names no path, real or fake, at all. False for anything that could be a real
    username."""
    rest = text[start + len("/Users/"):]
    if rest.startswith(("<", "$")):
        return True                                          # a template, or a shell expansion like /Users/$(whoami)
    name = re.split(r"[/'\")\s]", rest, maxsplit=1)[0]
    return not name or name in FAKE_USER_NAMES


def _is_test_file(rel: str) -> bool:
    """Whether `rel` is a test file whose whole job is proving a fake secret/path gets redacted
    (tests_agentlab/**, and any *.test.js / *.test.jsx / *.dom.test.jsx frontend test)."""
    parts = Path(rel).parts
    if parts and parts[0] == "tests_agentlab":
        return True
    return ".test." in Path(rel).name


def scan_for_leaks(root: Path) -> list[str]:
    """Every finding that is not an allowed fake: `path:line: what`."""
    home = os.path.expanduser("~")
    findings = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SCAN_SKIP_PARTS and not d.startswith(".")]
        for fname in filenames:
            path = Path(dirpath) / fname
            rel = path.relative_to(root).as_posix()
            if rel.startswith("data/runs/verify/"):
                continue                                        # this gate's own mock run, written on this machine: not shipped
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue                                        # binary or unreadable: not a text leak
            is_test = _is_test_file(rel)
            for lineno, line in enumerate(text.splitlines(), 1):
                # This build machine's actual home directory: never a fake, in ANY file.
                if home and home != "/" and home in line:
                    findings.append(f"{rel}:{lineno}: this build machine's home directory ({home})")
                if not is_test and MACHINE_TRACE_RE.search(line):
                    findings.append(f"{rel}:{lineno}: machine trace (per-user temp dir, or an ls owner column naming an account)")
                for i in (m.start() for m in re.finditer(r"/Users/", line)):
                    if _is_fake_users_path(line, i):
                        continue
                    findings.append(f"{rel}:{lineno}: /Users/ path that is not a known fake fixture")
                    break
                if is_test:
                    continue                                     # a redaction test's own synthetic secret
                for label, pattern in SECRET_PATTERNS.items():
                    if pattern.search(line):
                        findings.append(f"{rel}:{lineno}: looks like a real {label}")
    return findings


def verify(zip_path: Path) -> dict:
    verdict: list[dict] = []
    tmp = Path(tempfile.mkdtemp(prefix="hl-verify-"))
    extracted = tmp / "extracted"
    server = None
    try:
        with _check(verdict, "extract to a clean temp dir"):
            with zipfile.ZipFile(zip_path) as archive:
                archive.extractall(extracted)
            pkg = extracted / "harnesslab-school"
            if not pkg.is_dir():
                raise RuntimeError(f"expected harnesslab-school/ inside the archive, found: "
                                   f"{sorted(p.name for p in extracted.iterdir())}")

        pkg = extracted / "harnesslab-school"
        venv_dir = tmp / ".venv"
        py = venv_dir / "bin" / "python"
        bin_dir = venv_dir / "bin"

        with _check(verdict, "create a throwaway venv"):
            venv.EnvBuilder(with_pip=True).create(venv_dir)
            if not py.is_file():
                raise RuntimeError(f"no python at {py} after venv creation")

        with _check(verdict, "pip install -e . (editable, no other harnesslab on the path)"):
            r = _run([str(py), "-m", "pip", "install", "-e", "."], cwd=pkg, timeout=600)
            if r.returncode != 0:
                raise RuntimeError(f"pip install failed (exit {r.returncode}):\n{r.stdout[-4000:]}\n{r.stderr[-4000:]}")

        with _check(verdict, "harnesslab --version"):
            r = _run([str(bin_dir / "harnesslab"), "--version"], cwd=pkg)
            if r.returncode != 0 or "harnesslab" not in r.stdout.lower():
                raise RuntimeError(f"exit {r.returncode}: {r.stdout!r} {r.stderr!r}")

        port = free_port()
        base = f"http://127.0.0.1:{port}"
        with _check(verdict, f"start the server on 127.0.0.1:{port} (--no-browser)"):
            server = subprocess.Popen(
                [str(bin_dir / "harnesslab"), "--host", "127.0.0.1", "--port", str(port), "--no-browser"],
                cwd=pkg, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            deadline = time.time() + 30
            up = False
            last_err = None
            while time.time() < deadline:
                if server.poll() is not None:
                    raise RuntimeError(f"server exited early ({server.returncode}): {server.stdout.read()[-4000:]}")
                try:
                    urllib.request.urlopen(f"{base}/api/results", timeout=2).read()
                    up = True
                    break
                except (urllib.error.URLError, ConnectionError) as e:
                    last_err = e
                    time.sleep(0.5)
            if not up:
                raise RuntimeError(f"server did not answer /api/results within 30s: {last_err}")

        with _check(verdict, "GET /api/results matches the manifest's datasets and run counts"):
            scope = json.loads((pkg / "harnesslab" / "school-package.json").read_text())
            listed = json.loads(urllib.request.urlopen(f"{base}/api/results", timeout=10).read())
            names = {d["name"] for d in listed}
            wanted = set(scope["datasets"])
            if names != wanted:
                raise RuntimeError(f"dataset set mismatch: API has {sorted(names)}, manifest wants {sorted(wanted)}")
            mismatched = []
            for d in listed:
                idx = pkg / "data" / "runs" / d["name"] / "index.jsonl"
                n = sum(1 for line in idx.read_text().splitlines() if line.strip())
                if d["runs"] != n:
                    mismatched.append(f"{d['name']}: API says {d['runs']}, index.jsonl has {n}")
            if mismatched:
                raise RuntimeError("run count mismatch: " + "; ".join(mismatched))

        for label, args in [
            ("exercises/ex1_variance.py", ["exercises/ex1_variance.py"]),
            ("exercises/ex2_harness.py", ["exercises/ex2_harness.py"]),
            ("exercises/ex7_real_trajectories.py --offline", ["exercises/ex7_real_trajectories.py", "--offline"]),
            ("exercises/ex8_experiment.py", ["exercises/ex8_experiment.py"]),
        ]:
            with _check(verdict, f"{label} exits 0"):
                r = _run([str(py), *args], cwd=pkg, timeout=180)
                if r.returncode != 0:
                    raise RuntimeError(f"exit {r.returncode}:\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}")

        with _check(verdict, "harnesslab run --provider mock ... --out data/runs/verify exits 0"):
            r = _run([str(bin_dir / "harnesslab"), "run", "--provider", "mock", "--tasks", "t01_slugify",
                     "--repeats", "1", "--out", "data/runs/verify"], cwd=pkg, timeout=120)
            if r.returncode != 0:
                raise RuntimeError(f"exit {r.returncode}:\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}")

        with _check(verdict, f"shipped unittest suite: 0 failures, 0 errors, >= {MIN_SHIPPED_TESTS} ran"):
            r = _run([str(py), "-m", "unittest", "discover", "-s", "tests_agentlab", "-v"], cwd=pkg, timeout=600)
            tail = r.stdout[-6000:] + r.stderr[-6000:]
            m = re.search(r"Ran (\d+) tests?", r.stderr) or re.search(r"Ran (\d+) tests?", r.stdout)
            ran = int(m.group(1)) if m else 0
            ok = r.returncode == 0 and ran >= MIN_SHIPPED_TESTS
            if not ok:
                raise RuntimeError(f"exit {r.returncode}, ran={ran} (need >= {MIN_SHIPPED_TESTS}):\n{tail}")

        with _check(verdict, "every manifest file re-hashes to the value it records"):
            manifest = json.loads((pkg / "PACKAGE_MANIFEST.json").read_text())
            bad = []
            for entry in manifest["files"]:
                p = pkg / entry["path"]
                if not p.is_file():
                    bad.append(f"{entry['path']}: missing")
                    continue
                data = p.read_bytes()
                if len(data) != entry["bytes"]:
                    bad.append(f"{entry['path']}: {len(data)} bytes, manifest says {entry['bytes']}")
                elif hashlib.sha256(data).hexdigest() != entry["sha256"]:
                    bad.append(f"{entry['path']}: sha256 does not match the manifest")
            if bad:
                shown = "; ".join(bad[:20]) + (f" ... ({len(bad) - 20} more)" if len(bad) > 20 else "")
                raise RuntimeError(shown)

        with _check(verdict, "no machine paths or real-shaped secrets in the extracted tree"):
            found = scan_for_leaks(pkg)
            if found:
                shown = "; ".join(found[:20]) + (f" ... ({len(found) - 20} more)" if len(found) > 20 else "")
                raise RuntimeError(shown)
    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)

    return {"ok": all(c["ok"] for c in verdict), "zip": str(zip_path), "checks": verdict}


def main():
    zip_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "harnesslab-school.zip"
    if not zip_path.is_file():
        print(json.dumps({"ok": False, "zip": str(zip_path), "checks": [
            {"name": "zip exists", "ok": False, "detail": f"no such file: {zip_path}", "seconds": 0}]}, indent=2))
        raise SystemExit(1)
    verdict = verify(zip_path)
    print(json.dumps(verdict, indent=2))
    raise SystemExit(0 if verdict["ok"] else 1)


if __name__ == "__main__":
    main()
