"""The handoff must omit local data physically, not only hide its UI links."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

from scripts.build_school_package import build, check_no_silent_drops, selected, tracked_files


class SchoolPackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = {
            'title': 'School package', 'datasets': ['demo_mock'],
            'include': ['harnesslab/', 'SCHOOL_PACKAGE.md'],
            'exclude': ['harnesslab/data/'],
            'extra_files': ['harnesslab/data/models/mock_prerecorded.json'],
        }
        for name, content in {
            'harnesslab/school-package.json': json.dumps(self.scope),
            'harnesslab/frontend/dist/index.html': '<html>Dashboard</html>',
            'harnesslab/data/active_model.txt': 'private_local_model',
            'harnesslab/data/models/mock_prerecorded.json': '{}',
            'harnesslab/data/models/private_local_model.json': 'private training data',
            'harnesslab/data/openrouter.key': 'secret-key',
            'harnesslab/frontend/node_modules/vendor/index.js': 'vendor',
            'harnesslab/__pycache__/app.pyc': 'bytecode',
            'harnesslab/.env': 'SECRET=value',
            'SCHOOL_PACKAGE.md': 'School instructions',
            'data/runs/demo_mock/run-1/patch.diff': 'diff --git a/example.py b/example.py',
            'data/runs/demo_mock/index.jsonl': '{"run_id":"demo"}\n',
            'data/runs/captured/index.jsonl': 'private session',
            'data/runs/live/index.jsonl': 'local experiment',
        }.items():
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)

    def test_archive_contains_only_selected_data_and_a_verifiable_inventory(self):
        out = self.root / 'school.zip'
        build(self.root, out)
        with zipfile.ZipFile(out) as archive:
            files = {name.removeprefix('harnesslab-school/'): archive.read(name) for name in archive.namelist()}
        text = b'\n'.join(files.values())
        for forbidden in (b'private_local_model', b'secret-key', b'private session', b'local experiment', b'SECRET=value', b'bytecode'):
            self.assertNotIn(forbidden, text)
        self.assertNotIn('harnesslab/frontend/node_modules/vendor/index.js', files)
        self.assertEqual(files['harnesslab/data/active_model.txt'], b'mock_prerecorded\n')
        self.assertEqual((self.root / 'harnesslab/data/active_model.txt').read_text(), 'private_local_model')
        self.assertEqual(files['README.md'], b'School instructions')
        self.assertIn('data/runs/demo_mock/run-1/patch.diff', files)
        inventory = json.loads(files['PACKAGE_MANIFEST.json'])
        self.assertEqual(len(inventory['files']), len(files) - 1)
        for item in inventory['files']:
            self.assertEqual(hashlib.sha256(files[item['path']]).hexdigest(), item['sha256'])

    def test_missing_included_data_fails_instead_of_silently_shipping_less(self):
        (self.root / 'data/runs/demo_mock/index.jsonl').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing dataset'):
            build(self.root, self.root / 'school.zip')

    def test_included_symlink_is_rejected(self):
        (self.root / 'harnesslab/aliased.jsonl').symlink_to(self.root / 'data/runs/captured/index.jsonl')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            build(self.root, self.root / 'school.zip')

    def test_a_file_with_an_unanticipated_suffix_still_ships(self):
        """Regression guard: an earlier suffix allow-list silently dropped
        tests_agentlab/fixtures/.../pandas-dev__pandas-51234.traj and .../tiny_swe.eval -- fixtures
        whose extension nobody had anticipated -- with no error at build time. `selected()` must
        ship anything under an included root regardless of extension; only `exclude`,
        hidden-file/cache skipping, and symlink rejection may drop something."""
        odd = self.root / 'harnesslab/plugins/notes.xyz'
        odd.parent.mkdir(parents=True, exist_ok=True)
        odd.write_text('#not a suffix this builder was told about\n')
        out = self.root / 'school.zip'
        build(self.root, out)
        with zipfile.ZipFile(out) as archive:
            names = {n.removeprefix('harnesslab-school/') for n in archive.namelist()}
        self.assertIn('harnesslab/plugins/notes.xyz', names)

    def test_selected_never_rejects_on_suffix_alone(self):
        """Direct check on the predicate itself, not just the archive it produces."""
        self.assertTrue(selected('harnesslab/plugins/rule.some-weird-ext', self.scope))
        self.assertTrue(selected('harnesslab/data/models/mock_prerecorded.json', self.scope))  # extra_files
        self.assertFalse(selected('harnesslab/data/models/other.json', self.scope))            # excluded root
        self.assertFalse(selected('harnesslab/.hidden/thing.py', self.scope))                  # hidden part


class NoSilentDropTests(unittest.TestCase):
    """The build-time backstop: a git-tracked file under an included root must either ship or be
    covered by an explicit `exclude` entry, or the build fails loudly instead of shipping a
    quietly-incomplete package. Exercised against a real git repo, not a mock of `git ls-files`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = {
            'title': 'School package', 'datasets': ['demo_mock'],
            'include': ['harnesslab/', 'SCHOOL_PACKAGE.md'],
            'exclude': ['harnesslab/data/'],
            'extra_files': ['harnesslab/data/models/mock_prerecorded.json'],
        }
        for name, content in {
            'harnesslab/school-package.json': json.dumps(self.scope),
            'harnesslab/frontend/dist/index.html': '<html>Dashboard</html>',
            'harnesslab/data/active_model.txt': 'private_local_model',
            'harnesslab/data/models/mock_prerecorded.json': '{}',
            'SCHOOL_PACKAGE.md': 'School instructions',
            'data/runs/demo_mock/index.jsonl': '{"run_id":"demo"}\n',
        }.items():
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)

    def git(self, *args):
        subprocess.run(['git', *args], cwd=self.root, check=True, capture_output=True, text=True)

    def init_repo(self):
        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'Test')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'snapshot')

    def test_not_a_git_checkout_returns_none_rather_than_a_false_pass_or_fail(self):
        """A verdict of "no drops" over a corpus the check never actually examined would be worse
        than saying nothing: build() reports the check was skipped instead of claiming a result."""
        self.assertIsNone(check_no_silent_drops(self.root, self.scope, shipped=set()))
        self.assertIsNone(tracked_files(self.root))

    def test_a_tracked_file_missing_from_the_working_tree_fails_the_build(self):
        """Exactly the shape of bug this check exists for: git's index still lists a file under an
        included root (committed, never `git rm`-ed) that the physical walk cannot find -- a typo'd
        include root, a broken rglob, or (as it actually happened) a suffix filter would otherwise
        ship a quietly incomplete package with no error at all."""
        self.init_repo()
        extra = self.root / 'harnesslab' / 'plugins' / 'extra_rule.py'
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_text('# a rule the plugin loader expects to find\n')
        self.git('add', 'harnesslab/plugins/extra_rule.py')
        self.git('commit', '-q', '-m', 'add extra rule')
        extra.unlink()                                    # tracked, but gone from the working tree
        with self.assertRaisesRegex(ValueError, 'silently dropped'):
            build(self.root, self.root / 'school.zip')

    def test_the_same_missing_file_under_an_explicit_exclude_does_not_trip_the_check(self):
        """The check must not fight the selection policy: a file under `exclude` is a deliberate
        omission, not a silent one, even when git still has it and the working tree does not."""
        self.init_repo()
        excluded = self.root / 'harnesslab' / 'data' / 'stale_note.txt'
        excluded.write_text('developer scratch note\n')
        self.git('add', 'harnesslab/data/stale_note.txt')
        self.git('commit', '-q', '-m', 'add a note under the excluded data/ root')
        excluded.unlink()
        result = build(self.root, self.root / 'school.zip')
        self.assertGreater(result['files'], 0)             # did not raise: the exclude covers it

    def test_a_tracked_file_outside_every_included_root_is_not_in_scope_at_all(self):
        """A file the manifest never claims to include (nothing under `include`/`extra_files`
        names it) is not a drop -- it was never promised, so there is nothing to catch here."""
        self.init_repo()
        outside = set(check_no_silent_drops(self.root, self.scope, shipped=set()) or [])
        self.assertNotIn('.gitignore', outside)             # not created, but illustrates the point
        # A tracked file at the repo root, matching no include/extra_files entry:
        (self.root / 'NOTES.md').write_text('not part of any included root\n')
        self.git('add', 'NOTES.md')
        self.git('commit', '-q', '-m', 'add an out-of-scope note')
        missing = check_no_silent_drops(self.root, self.scope, shipped=set())
        self.assertNotIn('NOTES.md', missing)
