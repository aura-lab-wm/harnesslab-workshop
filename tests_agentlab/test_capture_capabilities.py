"""A descriptor DECLARES what its source records, and the declaration is held to its fixture.

`has_call_ids` was the only declared fact. A source that records no tool results, no usage or no
clock had nowhere to say so, so the writer filled the gaps with zeros, "ok" statuses and estimates,
and a run that could not see its tools looked like a run that chose not to use any. The declaration
now covers all four facts, and the check is EQUALITY in both directions: a descriptor that claims a
fact its fixture cannot demonstrate is rejected, and so is one that hides a fact its fixture has.
"""
import dataclasses
import os
import sys
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.backend.importers import cursor                 # noqa: E402
from harnesslab.backend.importers.common import Event           # noqa: E402
from harnesslab.capture import adapters, identity               # noqa: E402

FIXDIR = os.path.join(LAB, "tests_agentlab", "fixtures", "capture")


def events_of(ad_module, fixture):
    return identity.dedup_events(next(iter(ad_module.sessions(os.path.join(FIXDIR, fixture)))).events)


FULL = [Event(kind="user", ts="2026-09-10T10:00:00Z", text="<p>"),
        Event(kind="tool_call", ts="2026-09-10T10:00:01Z", call_id="c1", name="Read",
              usage={"input_tokens": 1, "output_tokens": 1}),
        Event(kind="tool_result", ts="2026-09-10T10:00:02Z", call_id="c1", text="<r>")]

PARTIAL_DESCRIPTOR = adapters.CaptureAdapter(
    name="cursor", module=cursor, description="", fixture="cursor_transcript.jsonl", golden="",
    has_call_ids=False, has_tool_spans=False, has_usage=False, has_timestamps=False)


class TestDeclarationsAreHeldToTheFixture(unittest.TestCase):
    def test_every_registered_descriptor_declares_exactly_what_its_fixture_shows(self):
        for ad in adapters.REGISTRY:
            with self.subTest(adapter=ad.name):
                self.assertEqual(adapters.capability_mismatches(ad, events_of(ad.module, ad.fixture)), [])

    def test_a_partial_source_is_accepted_for_exactly_what_it_records(self):
        ev = events_of(cursor, "cursor_transcript.jsonl")
        self.assertEqual(adapters.capability_mismatches(PARTIAL_DESCRIPTOR, ev), [])

    def test_claiming_any_fact_the_fixture_cannot_demonstrate_is_rejected(self):
        ev = events_of(cursor, "cursor_transcript.jsonl")
        for flag in ("has_call_ids", "has_tool_spans", "has_usage", "has_timestamps"):
            with self.subTest(claims=flag):
                over = dataclasses.replace(PARTIAL_DESCRIPTOR, **{flag: True})
                problems = adapters.capability_mismatches(over, ev)
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(flag, problems[0])

    def test_hiding_a_fact_the_fixture_has_is_rejected_too(self):
        ad = adapters.BY_NAME["claude_code"]
        for flag in ("has_call_ids", "has_tool_spans", "has_usage", "has_timestamps"):
            with self.subTest(hides=flag):
                under = dataclasses.replace(ad, **{flag: False})
                self.assertEqual(len(adapters.capability_mismatches(under, FULL)), 1)

    def test_tool_calls_with_no_results_are_not_tool_spans(self):
        ad = dataclasses.replace(PARTIAL_DESCRIPTOR, has_call_ids=True, has_usage=True, has_timestamps=True)
        calls_only = [e for e in FULL if e.kind != "tool_result"]
        self.assertEqual(adapters.capability_mismatches(ad, calls_only), [])

    def test_missing_names_what_a_descriptor_cannot_measure(self):
        self.assertEqual(adapters.missing(PARTIAL_DESCRIPTOR), frozenset({"tool_spans", "usage", "timestamps"}))
        self.assertEqual(adapters.missing(adapters.BY_NAME["claude_code"]), frozenset())

    def test_the_capture_page_is_told_every_declaration(self):
        for row in adapters.describe():
            with self.subTest(adapter=row["name"]):
                for flag in ("has_call_ids", "has_tool_spans", "has_usage", "has_timestamps"):
                    self.assertIsInstance(row[flag], bool)


if __name__ == "__main__":
    unittest.main()
