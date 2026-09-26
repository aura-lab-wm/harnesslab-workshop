"""Dynamic packaging bits. All metadata lives in pyproject.toml; only what a static table
cannot express is here.

Two things need code:

1. **Discovery that survives new modules.** The backend grows subpackages
   (``harnesslab.backend.importers``, ...); a hand-maintained ``packages`` list in pyproject.toml
   silently ships a wheel that imports on the developer's checkout and crashes on a user's
   machine. ``find_packages`` restricted to ``harnesslab*`` cannot pick up the task
   repositories under ``tasks/`` or anything in ``node_modules/`` (neither has ``__init__.py``).

2. **Data that lives outside any package.** ``harnesses/`` and ``tasks/`` sit at the repository
   root. They are mapped under ``harnesslab/bundle/`` in the wheel so an installed harnesslab can
   seed a workspace on first run -- see ``harnesslab/backend/paths.py``.

A data-only entry with no ``__init__.py`` is never picked up by ``find_packages``, so it is listed
by hand below -- but setuptools' ``build_py.check_package()`` hard-fails the whole install
(``error: package directory '...' does not exist``) the moment ONE of these directories is
missing, even though every real Python package still imports fine. That turns a curated,
partial checkout (a school handoff, an sdist trimmed by an allow-list, anyone who deletes an
optional directory) into a total install failure over what is, for the code, an empty/optional
data package. Filter the list down to directories that actually exist on THIS checkout before
handing it to ``setup()``: a directory present here (as it is for every release checkout, where
``harnesslab/docs`` etc. all exist) still ships in the wheel exactly as before; one genuinely
absent is skipped with a warning instead of failing the install.
"""
import sys
from pathlib import Path

from setuptools import find_packages, setup

ROOT = Path(__file__).resolve().parent

# Real Python packages (they have __init__.py).
PACKAGES = find_packages(include=["harnesslab", "harnesslab.*"])

# package_dir overrides for the data packages that do not live at their dotted-path location.
DATA_PACKAGE_DIR = {
    "harnesslab.bundle.harnesses": "harnesses",
    "harnesslab.bundle.tasks": "tasks",
}

# Data-only directories that must still be laid down in the wheel, when present on this checkout.
DATA_PACKAGES_WANTED = [
    "harnesslab.data",              # active_model.txt
    "harnesslab.data.models",       # trained sentinel models
    "harnesslab.frontend",          # the built UI, under dist/
    "harnesslab.plugins",           # sentinel rule plugins (sentinel.PLUGINS_DIR is package-relative)
    "harnesslab.docs",              # prose the app links to
    "harnesslab.bundle.harnesses",  # <- harnesses/
    "harnesslab.bundle.tasks",      # <- tasks/
]


def _existing(root: Path, wanted: list[str], package_dir: dict[str, str]) -> list[str]:
    present = []
    for name in wanted:
        rel = package_dir.get(name, name.replace(".", "/"))
        if (root / rel).is_dir():
            present.append(name)
        else:
            print(f"setup.py: {name!r} ({rel}) is not on this checkout -- skipping it "
                  f"(install continues; that data will simply be absent)", file=sys.stderr)
    return present


DATA_PACKAGES = _existing(ROOT, DATA_PACKAGES_WANTED, DATA_PACKAGE_DIR)

setup(
    packages=sorted(set(PACKAGES) | set(DATA_PACKAGES)),
    package_dir=DATA_PACKAGE_DIR,
)
