import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('publish_workshop_repo', ROOT / 'scripts/publish_workshop_repo.py')
pub = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pub)


class ReplaceTreeTest(unittest.TestCase):
    def _archive(self, tmp: Path, files: dict) -> Path:
        path = tmp / 'p.zip'
        with zipfile.ZipFile(path, 'w') as zf:
            for name, body in files.items():
                zf.writestr(name, body)
        return path

    def test_mirrors_package_and_keeps_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            dest = tmp / 'repo'
            (dest / '.git').mkdir(parents=True)
            (dest / '.git' / 'HEAD').write_text('ref')
            (dest / 'stale.txt').write_text('gone')
            (dest / 'old' / 'x').mkdir(parents=True)
            archive = self._archive(tmp, {
                'harnesslab-school/README.md': 'hi',
                'harnesslab-school/.devcontainer/devcontainer.json': '{}',
                'elsewhere/ignored.txt': 'no',
            })
            pub.replace_tree(dest, archive)
            names = sorted(str(p.relative_to(dest)) for p in dest.rglob('*') if p.is_file())
            self.assertEqual(names, ['.devcontainer/devcontainer.json', '.git/HEAD', 'README.md'])

    def test_rejects_escaping_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            dest = tmp / 'repo'
            dest.mkdir()
            archive = self._archive(tmp, {'harnesslab-school/../evil': 'x'})
            with self.assertRaises(ValueError):
                pub.replace_tree(dest, archive)


if __name__ == '__main__':
    unittest.main()
