"""Build the school handoff from the same allow-list the dashboard previews.

Run from any directory: python3 scripts/build_school_package.py
No Git history, API calls, captured data, or local configuration is copied.

--verify builds a fresh archive and then proves it, end to end, in a clean room: extracts it,
installs it in a throwaway venv, starts the server, and drives it -- see
scripts/verify_school_package.py for exactly what that checks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ROOT / 'harnesslab' / 'school-package.json'
SKIP_PARTS = {'node_modules', '__pycache__', '.git', '.venv', '.pytest_cache', '.DS_Store'}
#: Dot-directories that ship anyway: the dev container is how a workshop opens the package in
#: GitHub Codespaces (docs/WORKSHOP.md). Every other hidden file or directory stays out.
SHIPPED_DOT_DIRS = {'.devcontainer'}


def _hidden(parts) -> bool:
    return any(p in SKIP_PARTS or (p.startswith('.') and p not in SHIPPED_DOT_DIRS) for p in parts)


def _under_any_root(name: str, roots: list[str]) -> bool:
    return any(name == prefix or (prefix.endswith('/') and name.startswith(prefix)) for prefix in roots)


def selected(name: str, scope: dict) -> bool:
    """An explicit dataset list and file roots, followed by exclusions. Never follow symlinks.

    No suffix allow-list: a tracked file under an included root ships regardless of its
    extension. An allow-list here once silently dropped
    tests_agentlab/fixtures/importers/swe_agent/.../pandas-dev__pandas-51234.traj and
    tests_agentlab/fixtures/importers/tiny_swe.eval (fixtures whose extension nobody had
    anticipated) -- breaking the shipped test_importers detection tests without ANY error at
    build time. check_no_silent_drops() below is the build-time backstop for the same class of
    bug: it fails the build instead of shipping a quietly-incomplete package.
    """
    parts = Path(name).parts
    if _hidden(parts):
        return False
    if name in scope['extra_files']:
        return True
    if _under_any_root(name, scope['exclude']):
        return False
    if name.startswith('data/runs/'):
        return len(parts) > 3 and parts[2] in scope['datasets']
    return _under_any_root(name, scope['include'])


def files_for(root: Path, scope: dict) -> list[Path]:
    # Only walk named roots. In particular, never walk data/runs/captured or arbitrary local outputs.
    roots = [*scope['include'], *scope['extra_files'], *(f'data/runs/{d}/' for d in scope['datasets'])]
    files = set()
    for prefix in roots:
        base = root / prefix
        if base.is_symlink():
            raise ValueError(f'Package input is a symlink: {prefix}')
        if not base.exists():
            raise ValueError(f'Missing package input: {prefix}')
        candidates = base.rglob('*') if base.is_dir() else [base]
        for path in candidates:
            name = path.relative_to(root).as_posix()
            if not selected(name, scope):
                continue
            if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root and root in p.parents):
                raise ValueError(f'Package input is a symlink: {name}')
            if path.is_file():
                files.add(path)
    return sorted(files)


def tracked_files(root: Path) -> list[str] | None:
    """Every git-tracked file, repo-relative posix path. None when `root` is not a git checkout
    (or git itself is unavailable) -- the caller decides what that means for it."""
    try:
        out = subprocess.run(['git', 'ls-files', '-z'], cwd=root, check=True,
                              capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return [p for p in out.stdout.decode('utf-8', 'surrogateescape').split('\0') if p]


def check_no_silent_drops(root: Path, scope: dict, shipped: set[str]) -> list[str] | None:
    """Every git-tracked file that lives under an included root either ships or is covered by an
    explicit `exclude` entry. Anything else is a silent drop -- a typo'd include/exclude path, a
    file the walk does not reach, a selection-logic regression -- surfaced here as a build
    failure instead of shipping a package quietly missing part of what it claims to include.

    Returns the list of dropped paths (empty if none), or None if this is not a git checkout, in
    which case the check cannot run at all and the caller must say so rather than claim a result.
    """
    names = tracked_files(root)
    if names is None:
        return None
    roots = [*scope['include'], *scope['extra_files']]
    missing = []
    for name in names:
        parts = Path(name).parts
        if _hidden(parts):
            continue                                    # hidden-file/cache skipping: not a drop
        if name.startswith('data/runs/'):
            continue                                     # governed by the dataset allow-list
        if not _under_any_root(name, roots):
            continue                                     # not in scope at all
        if _under_any_root(name, scope['exclude']):
            continue                                     # explicitly excluded: not silent
        if name not in shipped:
            missing.append(name)
    return missing


# Real recorded runs carry traces of the machine they were collected on: the sandbox lives under
# macOS's per-user temp dir (/var/folders/<a>/<b>/T/...), and `ls -l` output in tool results names
# the account that owned the files. Neither is data the analysis uses (outcomes, token counts and
# timings are untouched), so the packaged copy rewrites both; the source directories are not modified.
_TMPDIR_RE = re.compile(rb'(?:/private)?/var/folders/[A-Za-z0-9_]+(?:/[A-Za-z0-9_+-]+)?(?:/T)?/?')  # also a path cut off mid-way by output truncation
_LS_OWNER_RE = re.compile(rb'([-dlcbps][-rwxsStT@+.]{9,11}\s+\d+\s+)([A-Za-z0-9_.-]+)(\s+)(staff|wheel|admin|everyone)\b')


def scrub_machine_traces(content: bytes) -> bytes:
    content = _TMPDIR_RE.sub(b'$TMPDIR/', content)
    return _LS_OWNER_RE.sub(lambda m: m.group(1) + (m.group(2) if m.group(2) == b'root' else b'user') + m.group(3) + m.group(4), content)


def build(root: Path, output: Path) -> dict:
    scope = json.loads((root / 'harnesslab/school-package.json').read_text())
    for name in scope['datasets']:
        if not (root / 'data/runs' / name / 'index.jsonl').is_file():
            raise ValueError(f'Missing dataset index: {name}')
    if not (root / 'harnesslab/frontend/dist/index.html').is_file():
        raise ValueError('Build the dashboard first: cd harnesslab/frontend && npm ci && npm run build')
    payload = {p.relative_to(root).as_posix(): p.read_bytes() for p in files_for(root, scope)}
    dropped = check_no_silent_drops(root, scope, set(payload))
    if dropped is None:
        print('no-silent-drop check skipped: not a git checkout (or git is unavailable)')
    elif dropped:
        shown = ', '.join(dropped[:20]) + (f', ... ({len(dropped) - 20} more)' if len(dropped) > 20 else '')
        raise ValueError(f'{len(dropped)} tracked file(s) under an included root would be silently '
                          f'dropped (not shipped, not explicitly excluded): {shown}')
    for name in payload:
        if name.startswith('data/runs/'):
            payload[name] = scrub_machine_traces(payload[name])
    # Never carry this machine's selected sentinel into the handoff.
    payload['harnesslab/data/active_model.txt'] = b'mock_prerecorded\n'
    payload['README.md'] = (root / 'SCHOOL_PACKAGE.md').read_bytes()
    inventory = {'title': scope['title'], 'scope': scope, 'files': [
        {'path': name, 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
        for name, content in sorted(payload.items())]}
    payload['PACKAGE_MANIFEST.json'] = (json.dumps(inventory, indent=2) + '\n').encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(payload.items()):
            entry = zipfile.ZipInfo('harnesslab-school/' + name, date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, content)
    return {'archive': str(output), 'files': len(payload), 'bytes': output.stat().st_size,
            'datasets': scope['datasets'], 'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'dist/harnesslab-school.zip')
    parser.add_argument('--verify', action='store_true',
                         help='after building, extract to a clean room and prove it installs and runs '
                              '(prints a JSON verdict; exits non-zero on any failed check)')
    args = parser.parse_args()
    result = build(ROOT, args.out)
    print(json.dumps(result, indent=2))
    if args.verify:
        from verify_school_package import verify   # same directory; see that module for the checks
        verdict = verify(args.out)
        print(json.dumps(verdict, indent=2))
        raise SystemExit(0 if verdict['ok'] else 1)


if __name__ == '__main__':
    main()
