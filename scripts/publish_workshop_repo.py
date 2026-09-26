"""Publish the school package as the public workshop repository.

    python3 scripts/publish_workshop_repo.py            # build, sync, commit, push
    python3 scripts/publish_workshop_repo.py --dry-run  # build and sync, show the diff, push nothing

Students open GitHub Codespaces from the public repository (docs/WORKSHOP.md). That repository
holds exactly what the school package holds -- the same allow-list, so no instructor solutions,
captured sessions, keys, local state or Git history -- and nothing else. This checkout stays
private; the public repository gets one commit per publish, naming the private commit it came from.

Needs `git`, and `gh` (logged in) only with --create for the first publish.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = 'aura-lab-wm/harnesslab-workshop'
DESCRIPTION = ('HarnessLab workshop: measure LLM coding agents as model x harness cells. '
               'Open in GitHub Codespaces; bring your own OpenRouter key.')
PACKAGE_DIR = 'harnesslab-school'


def run(cmd, cwd=None, capture=False) -> str:
    out = subprocess.run(cmd, cwd=cwd, check=True, text=True,
                         stdout=subprocess.PIPE if capture else None)
    return out.stdout.strip() if capture else ''


def replace_tree(dest: Path, archive: Path) -> None:
    """Make `dest` hold exactly the package: everything but .git goes, then the archive lands."""
    for child in dest.iterdir():
        if child.name == '.git':
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    prefix = PACKAGE_DIR + '/'
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if not info.filename.startswith(prefix) or info.is_dir():
                continue
            rel = info.filename[len(prefix):]
            if not rel or rel.startswith('/') or '..' in Path(rel).parts:
                raise ValueError(f'unsafe path in archive: {info.filename}')
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))
            mode = (info.external_attr >> 16) & 0o777
            if mode & 0o111:
                target.chmod(0o755)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--repo', default=REPO, help=f'owner/name (default {REPO})')
    parser.add_argument('--create', action='store_true', help='create the public repository first if missing')
    parser.add_argument('--dry-run', action='store_true', help='show what would change; push nothing')
    args = parser.parse_args()

    if run(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT, capture=True):
        raise SystemExit('Commit or stash tracked changes first: the public commit names a private commit.')
    source = run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture=True)

    with tempfile.TemporaryDirectory(prefix='hl-workshop-') as tmp:
        tmp = Path(tmp)
        archive = tmp / 'package.zip'
        run(['python3', str(ROOT / 'scripts/build_school_package.py'), '--out', str(archive)])

        if args.create and subprocess.run(['gh', 'repo', 'view', args.repo],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            run(['gh', 'repo', 'create', args.repo, '--public', '--description', DESCRIPTION])

        work = tmp / 'repo'
        run(['git', 'clone', '--quiet', f'https://github.com/{args.repo}.git', str(work)])
        replace_tree(work, archive)
        run(['git', 'add', '-A'], cwd=work)
        if not run(['git', 'status', '--porcelain'], cwd=work, capture=True):
            print(f'{args.repo} already matches {source}; nothing to publish.')
            return
        run(['git', '--no-pager', 'diff', '--cached', '--stat', '--stat-count=25'], cwd=work)
        if args.dry_run:
            print('Dry run: nothing pushed.')
            return
        run(['git', 'commit', '--quiet', '-m', f'Workshop package from harnesslab {source}'], cwd=work)
        run(['git', 'branch', '-M', 'main'], cwd=work)
        run(['git', 'push', '--quiet', 'origin', 'main'], cwd=work)
        print(f'Published https://github.com/{args.repo} from {source}.')
        print(f'Students open https://codespaces.new/{args.repo}')


if __name__ == '__main__':
    main()
