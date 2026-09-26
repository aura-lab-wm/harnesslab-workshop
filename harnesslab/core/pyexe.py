"""Find a Python interpreter that actually runs, and make it available as `python`.

Why this file exists. Both the grader and the agent's own `run_tests` shell out to
`python -m unittest`. On a stock macOS there is no `python` at all, and under pyenv
`python` is a shim that resolves per directory and can exit 127 while `which python`
happily prints a path. Either way every test run fails, the agent sees its own tests
failing, the grader marks every run a failure, and the sweep reads 0% with a transcript
that looks fine. That failure costs hours to diagnose, so it is worth a file.

Two rules, learned the hard way:
  * probe by EXECUTING the candidate, not by asking `shutil.which` whether it exists;
  * probe from the system temp directory, because pyenv resolves per directory and a
    probe run from inside the repository can answer differently than the real call.

`shim_dir()` returns a directory holding `python` and `python3` pointing at the winner;
putting it first on PATH means task commands can keep saying `python` and simply work.
"""
from __future__ import annotations
import os, shutil, subprocess, sys, tempfile

_CACHE: dict = {}
_PROBE = "import sys; sys.stdout.write('%d.%d' % sys.version_info[:2])"


def _works(exe: str) -> bool:
    """Does this interpreter actually start, and is it Python 3.8+?"""
    try:
        r = subprocess.run([exe, "-c", _PROBE], capture_output=True, text=True, timeout=15,
                           cwd=tempfile.gettempdir())   # neutral cwd: pyenv resolves per directory
    except (OSError, subprocess.SubprocessError):
        return False
    if r.returncode != 0:
        return False
    try:
        major, minor = (int(x) for x in r.stdout.strip().split(".")[:2])
    except ValueError:
        return False
    return (major, minor) >= (3, 8)


def interpreter() -> str:
    """Absolute path to an interpreter that runs. Falls back to sys.executable."""
    if "exe" in _CACHE:
        return _CACHE["exe"]
    candidates = [sys.executable]
    for name in ("python3", "python"):
        p = shutil.which(name)
        if p and p not in candidates:
            candidates.append(p)
    for c in candidates:
        if c and _works(c):
            _CACHE["exe"] = c
            return c
    _CACHE["exe"] = sys.executable      # nothing probed clean; the caller will see the error itself
    return _CACHE["exe"]


def shim_dir() -> str:
    """A directory with `python`/`python3` pointing at a working interpreter.

    Prepend it to PATH and a command that says `python -m unittest` works on a machine
    that has no `python`, or whose `python` is a broken shim."""
    if "dir" in _CACHE:
        return _CACHE["dir"]
    exe = interpreter()
    d = tempfile.mkdtemp(prefix="agentlab_py_")
    for name in ("python", "python3"):
        target = os.path.join(d, name + (".exe" if os.name == "nt" else ""))
        try:
            if os.name == "nt":
                with open(os.path.join(d, name + ".bat"), "w") as f:
                    f.write(f'@echo off\r\n"{exe}" %*\r\n')
            else:
                os.symlink(exe, target)
        except OSError:
            try:
                shutil.copy2(exe, target)
            except OSError:
                pass
    _CACHE["dir"] = d
    return d


def env(base: dict | None = None) -> dict:
    """`base` (default os.environ) with the shim first on PATH."""
    e = dict(os.environ if base is None else base)
    e["PATH"] = shim_dir() + os.pathsep + e.get("PATH", "")
    return e


def describe() -> str:
    exe = interpreter()
    return f"{exe} ({subprocess.run([exe, '-c', _PROBE], capture_output=True, text=True).stdout.strip()})"


if __name__ == "__main__":
    print("interpreter:", describe())
    print("shim dir:   ", shim_dir())
    r = subprocess.run("python -c \"import sys;print('shimmed python ->', sys.executable)\"",
                       shell=True, capture_output=True, text=True, env=env(), cwd=tempfile.gettempdir())
    print((r.stdout + r.stderr).strip())
