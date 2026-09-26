"""The conformance kit (spec 6.4). Every capture adapter must pass every check here.

CASES is generated from adapters.REGISTRY, so a descriptor cannot be added without a fixture and
a golden. Plan 2 adds Codex, Qwen Code, Gemini CLI and (if its census clears) Cursor.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.backend.importers import common as C            # noqa: E402
from harnesslab.capture import adapters, identity, regen        # noqa: E402

FIXDIR = os.path.join(LAB, "tests_agentlab", "fixtures", "capture")
CASES = [(a.name, os.path.join(FIXDIR, a.fixture), os.path.join(FIXDIR, a.golden))
         for a in adapters.REGISTRY]


def ledgers(out):
    result = {}
    for name in sorted(os.listdir(out)):
        with open(os.path.join(out, name, "ledger.jsonl"), "rb") as f:
            result[name] = f.read()
    return result


class TestConformance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hl-conformance-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_every_adapter(self):
        for adapter, fixture, golden in CASES:
            with self.subTest(adapter=adapter):
                ad = adapters.BY_NAME[adapter]
                a = os.path.join(self.tmp, adapter, "a")
                b = os.path.join(self.tmp, adapter, "b")
                runs = regen.regenerate(fixture, a, gap_s=1800, adapter=adapter)
                regen.regenerate(fixture, a, gap_s=1800, adapter=adapter)   # re-ingest in place
                regen.regenerate(fixture, b, gap_s=1800, adapter=adapter)   # a fresh directory
                la, lb = ledgers(a), ledgers(b)

                # 1. re-ingest is a no-op, and 2. the output is deterministic
                self.assertEqual(la, lb)

                spans = [json.loads(x) for x in la[runs[0].run_id].decode("utf-8").splitlines()]
                parsed = next(iter(regen.ADAPTERS[adapter].sessions(fixture)))
                events = identity.dedup_events(parsed.events)
                calls = [e for e in events if e.kind == "tool_call"]

                # 8. every DECLARED fact is held to reality, in both directions. Without this, an
                #    adapter that loses every id passes check 3 by collapsing {""} to a set of size
                #    one, and one that records no tool results, usage or clock can claim it does.
                self.assertEqual(adapters.capability_mismatches(ad, events), [])

                # 3. duplicated record ids emit once: one execute_tool span per distinct call
                n_exec = sum(1 for s in spans if s["span"] == "execute_tool")
                if not ad.has_tool_spans:
                    self.assertEqual(n_exec, 0, f"{adapter}: declares no tool spans but wrote some")
                elif ad.has_call_ids:
                    self.assertEqual(n_exec, len({e.call_id for e in calls}))
                else:
                    self.assertEqual(n_exec, len(calls))

                # 14. what the source does not record is UNKNOWN in the ledger, not zero: nothing is
                #     invented for an execution nobody saw, the fields it would have filled are null,
                #     the end span names them, and the REQUESTS themselves are still not lost.
                gaps = adapters.missing(ad)
                self.assertEqual(spans[-1]["unmeasured"], C.unmeasured_fields(gaps))
                for field in C.unmeasured_fields(gaps):
                    got = getattr(runs[0].summary, field)
                    if field in C.UNMEASURED_FIELDS["timestamps"]:
                        self.assertEqual(got, "", f"{adapter}: {field} was invented")
                    else:
                        self.assertIsNone(got, f"{adapter}: {field} reads {got!r}, not unknown")
                if not ad.has_tool_spans:
                    requested = sum(len(s["tool_calls"]) for s in spans if s["span"] == "chat")
                    self.assertEqual(requested, len(calls),
                                     f"{adapter}: tool requests vanished with their executions")

                # 4. tokens counted once per request_id
                req = [s["request_id"] for s in spans if s["span"] == "chat" and s["request_id"]]
                self.assertEqual(len(req), len(set(req)))

                # 5. every execute_tool span carries step and turn
                for s in spans:
                    if s["span"] == "execute_tool":
                        self.assertIn("step", s)
                        self.assertIn("turn", s)

                # 6. no unknown record types on the fixture
                self.assertEqual(spans[-1]["unknown_record_types"], {})

                # 9. usage fidelity: a source that records token counts is never estimated away
                if any(e.usage for e in events):
                    self.assertGreater(
                        sum(s["gen_ai.usage.input_tokens"] for s in spans if s["span"] == "chat"), 0,
                        f"{adapter}: the source carries usage but every chat span reads zero")

                # 10. anything that happened carries a timestamp -- or, for a source that declares
                #     it has no clock, nothing does. Openness is decided from last_ts (backfill.run),
                #     so a clockless adapter that did not SAY so would park every run in
                #     inflight.json forever and look like it worked; one that fabricated a clock
                #     would be worse.
                for s in spans:
                    if s["span"] in ("chat", "execute_tool"):
                        if ad.has_timestamps:
                            self.assertTrue(s["ts"],
                                            f"{adapter}: {s['span']} step {s.get('step')} has no ts")
                        else:
                            self.assertEqual(s["ts"], "", f"{adapter}: a clock was invented")

                # 11. an edit whose path the source knows must not land as "(unknown)"
                for s in spans:
                    if s["span"] == "edit":
                        self.assertNotEqual(s["path"], "(unknown)",
                                            f"{adapter}: edit at step {s['step']} lost its path")

                # 12. no native tool falls into the unnamed-tool fallback (common.normalize_tool),
                #     which glues "<name>: " onto the command and hides it from every tool facet
                ov = parsed.extra.get("tool_overrides") or {}
                for e in calls:
                    key = (e.name or "").lower()
                    self.assertTrue(key in ov or key in C.TOOL_MAP,
                                    f"{adapter}: native tool {e.name!r} is in no tool map")

                # 13. the fixture resolves to THIS adapter and to no other
                self.assertEqual(adapters.resolve(fixture)[0], adapter)

                # 7. the golden ledger, byte for byte
                with open(golden, "rb") as f:
                    self.assertEqual(la[runs[0].run_id], f.read())


if __name__ == "__main__":
    unittest.main()
