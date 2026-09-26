"""Pure identity rules for captured sessions (spec §5.2, §5.3)."""
import os
import sys
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.backend.importers import claude_code                  # noqa: E402
from harnesslab.backend.importers.common import Event as E, Session   # noqa: E402
from harnesslab.capture import identity                               # noqa: E402

FIX = os.path.join(LAB, "tests_agentlab", "fixtures", "capture", "cc_session.jsonl")


class TestDedup(unittest.TestCase):
    def test_first_occurrence_wins_and_idless_events_are_kept(self):
        ev = [E("tool_call", call_id="a", record_id="u1:0"), E("user", text="x"), E("user", text="y"),
              E("tool_call", call_id="b", record_id="u1:0"), E("tool_call", call_id="c", record_id="u1:1")]
        self.assertEqual([e.call_id or e.text for e in identity.dedup_events(ev)], ["a", "x", "y", "c"])

    def test_the_fixture_duplicate_is_dropped(self):
        sess = claude_code.parse_file(FIX)
        self.assertEqual(sum(1 for e in identity.dedup_events(sess.events) if e.kind == "tool_call"), 5)


class TestConvUuids(unittest.TestCase):
    def test_row_uuids_in_file_order_without_block_suffix(self):
        ev = [E("user", record_id="u1:0"), E("assistant", record_id="a1:0"), E("tool_call", record_id="a1:1"),
              E("meta", record_id="m1:0"), E("tool_result", record_id="r1:0"), E("user", record_id="")]
        self.assertEqual(identity.conv_uuids(ev), ["u1", "a1", "r1"])


def ev(kind, ts, **kw):
    return E(kind, ts=ts, **kw)


class TestSegments(unittest.TestCase):
    def test_a_long_gap_before_a_user_message_starts_a_segment(self):
        events = [ev("user", "2026-09-10T10:00:00Z"), ev("assistant", "2026-09-10T10:01:00Z"),
                  ev("user", "2026-09-15T08:00:00Z"), ev("assistant", "2026-09-15T08:00:30Z")]
        self.assertEqual(identity.segment_bounds(events, 1800), [(0, 2), (2, 4)])

    def test_a_long_tool_run_does_not_split_a_call_from_its_result(self):
        events = [ev("user", "2026-09-10T10:00:00Z"), ev("tool_call", "2026-09-10T10:00:05Z", call_id="c"),
                  ev("tool_result", "2026-09-10T11:30:00Z", call_id="c"), ev("assistant", "2026-09-10T11:30:05Z")]
        self.assertEqual(identity.segment_bounds(events, 1800), [(0, 4)])

    def test_a_backward_timestamp_jump_is_a_zero_gap(self):
        # measured against the latest time seen, 10:05 is five minutes after 10:00, not 5h47 after 04:18
        events = [ev("user", "2026-09-10T10:00:00Z"), ev("assistant", "2026-09-10T04:18:00Z"),
                  ev("user", "2026-09-10T10:05:00Z")]
        self.assertEqual(identity.segment_bounds(events, 1800), [(0, 3)])

    def test_empty(self):
        self.assertEqual(identity.segment_bounds([], 1800), [])

    def test_split_session_assigns_segments_and_gives_the_verdict_to_the_last(self):
        events = [ev("user", "2026-09-10T10:00:00Z", text="a"), ev("user", "2026-09-12T10:00:00Z", text="b")]
        s = Session(source="claude_code", session_id="s", events=events, hidden_pass=True,
                    outcome_source="sidecar", patch="diff", exit_status="max_tokens")
        parts = identity.split_session(s, 1800)
        self.assertEqual([p.extra["segment"] for p in parts], [0, 1])
        self.assertEqual([(p.hidden_pass, p.patch, p.exit_status) for p in parts],
                         [(None, "", ""), (True, "diff", "max_tokens")])
        self.assertEqual([len(p.events) for p in parts], [1, 1])
        self.assertIsNone(s.extra.get("segment"))      # the original is untouched


class TestRelations(unittest.TestCase):
    def test_a_resume_supersedes_the_parent_it_contains(self):
        rel = identity.relations({"parent": ["p1", "p2", "p3"], "resumed": ["p1", "p2", "p3", "p4"]})
        self.assertEqual(rel["resumed"]["supersedes"], ["parent"])
        self.assertEqual(rel["parent"]["superseded_by"], ["resumed"])
        self.assertEqual(rel["parent"]["forked_from"], [])

    def test_an_abandoned_fork_is_kept_and_not_superseded(self):
        rel = identity.relations({"parent": ["p1", "p2", "p3", "p4"], "fork": ["p1", "p2", "f1"]})
        self.assertEqual(rel["fork"], {"supersedes": [], "superseded_by": [],
                                       "forked_from": [{"run_id": "parent", "shared_prefix_len": 2}]})
        self.assertEqual(rel["parent"]["forked_from"], [{"run_id": "fork", "shared_prefix_len": 2}])

    def test_newer_and_shorter_decides_nothing(self):
        rel = identity.relations({"a-old-long": ["p1", "p2", "p3", "p4", "p5"], "z-new-short": ["p1", "p2", "x"]})
        self.assertFalse(rel["a-old-long"]["superseded_by"] or rel["z-new-short"]["superseded_by"])

    def test_identical_conversations_keep_the_first_run_id(self):
        rel = identity.relations({"b": ["p1", "p2"], "a": ["p1", "p2"]})
        self.assertEqual((rel["a"]["supersedes"], rel["b"]["superseded_by"]), (["b"], ["a"]))

    def test_different_roots_and_empty_runs_are_unrelated(self):
        self.assertEqual(identity.relations({"x": ["p1"], "y": ["q1"], "z": []}), {})


if __name__ == "__main__":
    unittest.main()
