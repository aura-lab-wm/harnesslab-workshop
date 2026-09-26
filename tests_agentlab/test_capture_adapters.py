"""The capture registry: which adapter owns a file."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.capture import adapters, harness_config   # noqa: E402

FIXDIR = os.path.join(LAB, "tests_agentlab", "fixtures", "capture")


class TestResolve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hl-resolve-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, name, rows):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        return p

    def test_the_claude_code_fixture_resolves_to_claude_code(self):
        name, score = adapters.resolve(os.path.join(FIXDIR, "cc_session.jsonl"))
        self.assertEqual(name, "claude_code")
        self.assertGreaterEqual(score, 0.5)

    def test_an_unrelated_jsonl_resolves_to_nothing(self):
        p = self.write("nope.jsonl", [{"hello": "world"}, {"rows": [1, 2]}])
        self.assertEqual(adapters.resolve(p), ("", 0.0))

    def test_a_missing_file_resolves_to_nothing_without_raising(self):
        self.assertEqual(adapters.resolve(os.path.join(self.tmp, "gone.jsonl")), ("", 0.0))

    def test_an_empty_file_resolves_to_nothing(self):
        p = os.path.join(self.tmp, "empty.jsonl")
        open(p, "w").close()
        self.assertEqual(adapters.resolve(p), ("", 0.0))

    def test_the_rejects_gate_keeps_a_wrapped_envelope_out(self):
        """A Qwen Code chat carries Claude Code's envelope around a Gemini message body.
        claude_code.sniff scores 0.95 on it and claude_code.parse_file then returns None -- zero
        runs, no error, no unknown-record count. The descriptor's `rejects` paths make that loud.

        The property under test is that claude_code never claims this file. Before the Qwen Code
        descriptor was registered that showed up as "nobody claims it"; now it shows up as the
        stronger answer, "its real owner claims it". Asserting the owner as well as the
        non-owner is what keeps the rejects gate from being satisfied by a file that simply
        falls through everything."""
        rows = [{"type": "assistant", "uuid": f"q-{i}", "parentUuid": None, "sessionId": "s1",
                 "cwd": "/w", "version": "0.23.3", "provenance": "assistant_output",
                 "message": {"role": "model", "parts": [{"text": "hi"}]}} for i in range(4)]
        p = self.write("qwen_like.jsonl", rows)
        from harnesslab.backend.importers import claude_code
        self.assertGreaterEqual(claude_code.sniff(p), 0.5)      # the sniffer really is fooled
        self.assertNotEqual(adapters.resolve(p)[0], "claude_code")   # the registry is not
        self.assertEqual(adapters.resolve(p)[0], "qwen_code")        # and it goes to its owner


class TestDescriptors(unittest.TestCase):
    def test_every_descriptor_is_complete(self):
        """A descriptor without a fixture and a golden cannot be conformance-tested, so it may
        not exist."""
        for ad in adapters.REGISTRY:
            with self.subTest(adapter=ad.name):
                self.assertEqual(ad.name, ad.module.NAME)
                self.assertTrue(ad.description)
                self.assertTrue(ad.globs)
                # against the strategy table itself, never a literal list: a descriptor naming
                # a strategy nobody wrote raises KeyError on the first captured file
                self.assertIn(ad.policy, harness_config.POLICIES)
                self.assertTrue(os.path.exists(os.path.join(FIXDIR, ad.fixture)), ad.fixture)
                self.assertTrue(os.path.exists(os.path.join(FIXDIR, ad.golden)), ad.golden)

    def test_names_are_unique_and_by_name_matches_the_registry(self):
        names = [a.name for a in adapters.REGISTRY]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(adapters.BY_NAME), set(names))

    def test_describe_is_json_serialisable_and_names_where_sessions_live(self):
        rows = adapters.describe()
        json.dumps(rows)
        self.assertEqual({r["name"] for r in rows}, set(adapters.BY_NAME))
        for r in rows:
            self.assertIn("default_roots", r)
            self.assertIn("description", r)


class TestDiscover(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hl-discover-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "root")
        os.makedirs(os.path.join(self.root, "proj"))
        shutil.copy(os.path.join(FIXDIR, "cc_session.jsonl"),
                    os.path.join(self.root, "proj", "s1.jsonl"))
        with open(os.path.join(self.root, "proj", "junk.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"hello": "world"}) + "\n")

    def s1(self):
        return os.path.realpath(os.path.join(self.root, "proj", "s1.jsonl"))

    def test_discover_tags_each_file_with_the_adapter_that_claims_it(self):
        pairs, errors = adapters.discover([self.root])
        self.assertEqual(errors, [])
        self.assertEqual(pairs, [(self.s1(), "claude_code")])

    def test_a_file_claimed_by_no_descriptor_is_dropped_not_errored(self):
        pairs, errors = adapters.discover([self.root])
        self.assertEqual(errors, [])
        self.assertNotIn("junk.jsonl", "".join(p for p, _ in pairs))

    def test_a_symlink_out_of_an_allowed_root_is_refused(self):
        """The allow-list is a security boundary, not a convenience: a symlink inside an allowed
        directory must not pull in a file from outside it."""
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        shutil.copy(os.path.join(FIXDIR, "cc_session.jsonl"), os.path.join(outside, "leak.jsonl"))
        os.symlink(os.path.join(outside, "leak.jsonl"),
                   os.path.join(self.root, "proj", "leak.jsonl"))
        pairs, _ = adapters.discover([self.root])
        self.assertEqual([p for p, _ in pairs], [self.s1()])

    def test_a_single_file_root_is_discovered(self):
        p = os.path.join(self.root, "proj", "s1.jsonl")
        self.assertEqual(adapters.discover([p]), ([(self.s1(), "claude_code")], []))

    def test_the_same_file_reached_through_two_roots_is_reported_once(self):
        link = os.path.join(self.tmp, "alias")
        os.symlink(self.root, link)
        pairs, _ = adapters.discover([self.root, link])
        self.assertEqual(pairs, [(self.s1(), "claude_code")])

    def test_the_skip_hook_is_given_the_realpath_and_a_stat_and_is_obeyed(self):
        """The sniffer supplies its cursor rule here rather than walking the tree itself."""
        seen = []

        def skip(real, st):
            seen.append((real, st.st_size))
            return True

        pairs, errors = adapters.discover([self.root], skip=skip)
        self.assertEqual((pairs, errors), ([], []))
        # every allow-listed candidate, claimed or not, before anything is opened: that is what
        # lets the sniffer learn which sources it saw this pass without a second walk
        self.assertEqual(sorted(r for r, _ in seen),
                         sorted([self.s1(), os.path.realpath(
                             os.path.join(self.root, "proj", "junk.jsonl"))]))
        self.assertTrue(all(size > 0 for _, size in seen))

    def test_a_skipped_file_is_never_sniffed(self):
        with mock.patch.object(adapters, "resolve",
                               side_effect=AssertionError("sniffed a skipped file")):
            self.assertEqual(adapters.discover([self.root], skip=lambda real, st: True),
                             ([], []))

    def test_an_unreadable_candidate_is_an_error_reported_by_realpath_not_a_crash(self):
        bad = os.path.join(self.root, "proj", "locked.jsonl")
        shutil.copy(os.path.join(FIXDIR, "cc_session.jsonl"), bad)
        os.chmod(bad, 0)
        self.addCleanup(os.chmod, bad, 0o600)
        pairs, errors = adapters.discover([self.root])
        self.assertEqual([p for p, _ in pairs], [self.s1()])
        self.assertEqual([e["path"] for e in errors], [os.path.realpath(bad)])

    def test_a_missing_root_is_simply_empty(self):
        self.assertEqual(adapters.discover([os.path.join(self.tmp, "nope")]), ([], []))


class TestDiscoverSkipSeam(unittest.TestCase):
    """The sniffer's half of the seam, moved here from the deleted Task 4.

    The predicate is called once for EVERY allow-listed candidate, before anything is sniffed, so a
    closure that records the path gets "which sources did I see this pass" for free -- which is how
    a vanished source gets marked and how a skipped file is still counted.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hl-skipseam-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "root", "proj")
        os.makedirs(self.root)
        self.a = os.path.join(self.root, "a.jsonl")
        self.b = os.path.join(self.root, "b.jsonl")
        shutil.copy(os.path.join(FIXDIR, "cc_session.jsonl"), self.a)
        shutil.copy(os.path.join(FIXDIR, "cc_session.jsonl"), self.b)

    def test_skip_is_called_once_per_candidate_with_a_stat(self):
        seen = {}

        def skip(real, st):
            seen[real] = st.st_size
            return False

        pairs, errors = adapters.discover([os.path.dirname(self.root)], skip=skip)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(seen), sorted([os.path.realpath(self.a), os.path.realpath(self.b)]))
        self.assertTrue(all(v > 0 for v in seen.values()))
        self.assertEqual(sorted(p for p, _ in pairs), sorted(seen))

    def test_a_skipped_candidate_is_never_sniffed_and_never_returned(self):
        with mock.patch.object(adapters, "resolve",
                               side_effect=AssertionError("sniffed a skipped file")):
            pairs, errors = adapters.discover([self.root], skip=lambda real, st: True)
        self.assertEqual((pairs, errors), ([], []))

    def test_without_a_skip_predicate_nothing_changes(self):
        pairs, errors = adapters.discover([self.root])
        self.assertEqual([a for _, a in pairs], ["claude_code", "claude_code"])
        self.assertEqual(errors, [])

    def test_an_unreadable_candidate_is_reported_by_its_realpath(self):
        os.chmod(self.b, 0)
        self.addCleanup(os.chmod, self.b, 0o600)
        pairs, errors = adapters.discover([self.root])
        self.assertEqual([p for p, _ in pairs], [os.path.realpath(self.a)])
        self.assertEqual([e["path"] for e in errors], [os.path.realpath(self.b)])


if __name__ == "__main__":
    unittest.main()
