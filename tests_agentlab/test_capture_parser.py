"""Claude Code parser facts the capture spine needs. None of these change a manual import."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.backend.importers import claude_code   # noqa: E402

FIX = os.path.join(LAB, "tests_agentlab", "fixtures", "capture", "cc_session.jsonl")
CXFIX = os.path.join(LAB, "tests_agentlab", "fixtures", "capture", "cx_rollout.jsonl")


def _cx_rows():
    """The Codex fixture's records, with the handle closed -- the tests below reread it often."""
    with open(CXFIX, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def _cx_lines():
    with open(CXFIX, encoding="utf-8") as f:
        return list(f)


class TestParserFacts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sess = claude_code.parse_file(FIX)

    def test_session_level_facts(self):
        x = self.sess.extra
        self.assertEqual(x["permission_modes"], ["bypassPermissions"])
        self.assertEqual(x["entrypoint"], "cli")
        self.assertEqual(x["unknown_record_types"], {})
        self.assertEqual(x["parent_session_id"], "")
        self.assertEqual((self.sess.cwd, self.sess.session_id, self.sess.model),
                         ("/work/demo", "sess-cap-1", "claude-opus-5"))

    def test_record_and_request_ids(self):
        calls = [e for e in self.sess.events if e.kind == "tool_call"]
        self.assertEqual([(e.call_id, e.record_id, e.request_id) for e in calls[:2]],
                         [("toolu_1", "a-0002:0", "req_1"), ("toolu_2", "a-0003:0", "req_1")])

    def test_the_parser_stays_faithful_to_the_duplicated_row(self):
        """Dedup is the capture path's job (identity.dedup_events); the parser reports the file."""
        self.assertEqual(sum(1 for e in self.sess.events if e.kind == "tool_call"), 6)

    def test_usage_carries_cache_tokens(self):
        first = next(e for e in self.sess.events if e.kind == "assistant")
        self.assertEqual(first.usage, {"input_tokens": 3, "output_tokens": 120,
                                       "cache_read_input_tokens": 20000,
                                       "cache_creation_input_tokens": 1500})

    def test_structured_tool_results(self):
        res = {e.call_id: e for e in self.sess.events if e.kind == "tool_result"}
        self.assertEqual(res["toolu_3"].raw["structured_patch"][0]["lines"],
                         [" def slugify(s):", "-    return s", "+    return s.lower()"])
        self.assertEqual(res["toolu_3"].raw["file_path"], "/work/demo/slug.py")
        self.assertEqual((res["toolu_5"].raw["timed_out_ms"], res["toolu_5"].raw["interrupted"], res["toolu_5"].ok),
                         (120000, True, False))
        self.assertEqual(res["toolu_1"].raw, {})


class TestParserEdges(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hl-parser-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, rows, base=None):
        p = os.path.join(self.tmp, "s.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            if base:
                with open(base, encoding="utf-8") as src:
                    f.write(src.read())
            for r in rows:
                f.write(json.dumps(r) + "\n")
        return p

    def test_unknown_record_types_are_counted(self):
        p = self.write([{"type": "bridge-session", "sessionId": "sess-cap-1"}] * 2, base=FIX)
        self.assertEqual(claude_code.parse_file(p).extra["unknown_record_types"], {"bridge-session": 2})

    def test_max_tokens_stop_reason_still_sets_exit_status(self):
        p = self.write([
            {"type": "user", "uuid": "u1", "parentUuid": None, "sessionId": "s", "cwd": "/w", "version": "2.1.3",
             "timestamp": "2026-09-10T10:00:00.000Z", "message": {"role": "user", "content": "go"}},
            {"type": "assistant", "uuid": "a1", "parentUuid": "u1", "sessionId": "s", "requestId": "r1",
             "timestamp": "2026-09-10T10:00:01.000Z",
             "message": {"id": "m1", "role": "assistant", "model": "claude-opus-5", "stop_reason": "max_tokens",
                         "usage": {"input_tokens": 1, "output_tokens": 1}, "content": [{"type": "text", "text": "cut"}]}},
        ])
        self.assertEqual(claude_code.parse_file(p).exit_status, "max_tokens")

    def test_rows_appended_between_the_two_passes_are_not_seen(self):
        side = {"isSidechain": True, "sessionId": "parent", "agentId": "agent-1", "cwd": "/w", "version": "2.1.3"}
        p = self.write([
            dict(side, type="user", uuid="s1", timestamp="2026-09-10T10:00:00.000Z",
                 message={"role": "user", "content": "subagent task"}),
            dict(side, type="assistant", uuid="s2", requestId="r1", timestamp="2026-09-10T10:00:01.000Z",
                 message={"id": "m1", "role": "assistant", "model": "claude-opus-5",
                          "usage": {"input_tokens": 1, "output_tokens": 1},
                          "content": [{"type": "text", "text": "done"}]}),
        ])
        real = claude_code._iter_rows
        passes = []

        def growing(path, limit=None):
            yield from real(path, limit)
            passes.append(1)
            if len(passes) == 1:               # the file grows after the first pass
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"type": "user", "uuid": "late", "isSidechain": False, "sessionId": "parent",
                                        "timestamp": "2026-09-10T10:00:02.000Z",
                                        "message": {"role": "user", "content": "late row"}}) + "\n")

        with mock.patch.object(claude_code, "_iter_rows", growing):
            sess = claude_code.parse_file(p)
        self.assertTrue(sess.extra["standalone_sidechain"])
        self.assertNotIn("late row", [e.text for e in sess.events])


GEMFIX = os.path.join(LAB, "tests_agentlab", "fixtures", "capture", "gemini_session.jsonl")


class TestGeminiCli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from harnesslab.backend.importers import gemini_cli
        cls.mod = gemini_cli
        cls.sess = gemini_cli.parse_file(GEMFIX)

    def test_one_tool_call_produces_exactly_one_result(self):
        """A toolCalls entry fuses the call and its result, and the NEXT user row repeats the
        same result as a functionResponse. Emitting both would double-count every tool call."""
        calls = [e for e in self.sess.events if e.kind == "tool_call"]
        results = [e for e in self.sess.events if e.kind == "tool_result"]
        self.assertEqual([e.call_id for e in calls], ["gc-1"])
        self.assertEqual([e.call_id for e in results], ["gc-1"])

    def test_status_drives_ok_and_an_unknown_status_is_not_a_failure(self):
        self.assertIs([e for e in self.sess.events if e.kind == "tool_result"][0].ok, True)
        import json as _json, shutil as _sh, tempfile as _tf
        d = _tf.mkdtemp(prefix="hl-gem-")
        self.addCleanup(_sh.rmtree, d, True)
        p = os.path.join(d, "x.jsonl")
        rows = [_json.loads(l) for l in open(GEMFIX, encoding="utf-8")]
        for r in rows:
            for tc in (r.get("toolCalls") or []):
                tc["status"] = "cancelled"
        with open(p, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(_json.dumps(r) + "\n")
        self.assertIsNone([e for e in self.mod.parse_file(p).events
                           if e.kind == "tool_result"][0].ok)

    def test_per_turn_tokens_become_event_usage(self):
        used = [e.usage for e in self.sess.events if e.usage]
        self.assertEqual(used[0], {"input_tokens": 1500, "output_tokens": 90,
                                   "cache_read_input_tokens": 1200,
                                   "cache_creation_input_tokens": 0})

    def test_a_set_envelope_never_duplicates_a_message(self):
        """One source record id per emitted event. The plan wrote this as a check on the message
        id alone (`record_id.split(":")[0]`), which cannot hold on any adapter that splits one
        message into blocks -- `gm-2` legitimately emits a reasoning, an assistant, a call and a
        result. The property that is actually load-bearing, and that identity.dedup_events relies
        on, is that the FULL record_id is unique."""
        rids = [e.record_id for e in self.sess.events if e.record_id]
        self.assertEqual(len(rids), len(set(rids)))

    def test_a_set_envelope_carrying_a_new_message_lands_in_file_order(self):
        """Measured on the real corpus: 30 of 217 `$set` envelopes carry `messages`, 28 of those
        are followed by later typed rows, in 28 of 31 files, and none replays an id. Emitting them
        after every typed row therefore mis-orders at least one message in nearly every session --
        which is what "replace versus metadata" never asked about."""
        import json as _json, shutil as _sh, tempfile as _tf
        d = _tf.mkdtemp(prefix="hl-gem3-")
        self.addCleanup(_sh.rmtree, d, True)
        p = os.path.join(d, "x.jsonl")
        lines = list(open(GEMFIX, encoding="utf-8"))
        at = [i for i, ln in enumerate(lines) if '"id":"gm-4"' in ln.replace(" ", "")]
        self.assertEqual(len(at), 1)
        lines.insert(at[0], _json.dumps({"$set": {"messages": [
            {"id": "gm-3b", "type": "gemini", "timestamp": "2026-09-09T12:00:07.000Z",
             "model": "gemini-3.8-flash", "content": "Interleaved."}]}}) + "\n")
        open(p, "w", encoding="utf-8").writelines(lines)
        ids = [e.record_id.split(":", 1)[0] for e in self.mod.parse_file(p).events if e.record_id]
        self.assertIn("gm-3b", ids)
        self.assertLess(ids.index("gm-3b"), ids.index("gm-4"),
                        "a $set message belongs where its envelope appeared, not after the file")

    def test_a_set_envelope_that_replays_a_known_id_is_dropped(self):
        """Insurance against a build that starts replaying. On this corpus it never fires: zero of
        217 envelopes replayed an id a typed row already carried."""
        import json as _json, shutil as _sh, tempfile as _tf
        d = _tf.mkdtemp(prefix="hl-gem2-")
        self.addCleanup(_sh.rmtree, d, True)
        p = os.path.join(d, "x.jsonl")
        lines = list(open(GEMFIX, encoding="utf-8"))
        lines.append(_json.dumps({"$set": {"messages": [
            {"id": "gm-4", "type": "gemini", "timestamp": "2026-09-09T12:00:09.000Z",
             "content": "Fixed and the suite passes."}]}}) + "\n")
        open(p, "w", encoding="utf-8").writelines(lines)
        s = self.mod.parse_file(p)
        self.assertEqual(len([e for e in s.events if e.kind == "assistant"]),
                         len([e for e in self.sess.events if e.kind == "assistant"]))

    def test_a_message_reemitted_with_its_tool_calls_keeps_them_and_stays_in_place(self):
        """NOT in the plan and NOT in the census -- found by parsing the real corpus and getting
        zero tool calls out of 151. Gemini CLI writes an assistant message when its text lands and
        REWRITES the same id once its tool calls are dispatched. 253 typed rows over the 31 real
        sessions carry only 184 distinct ids; every repeated id appears exactly twice; content,
        tokens, type and timestamp are identical across the pair and `toolCalls` is the only
        difference, absent on the first emission and present on the second, in all 69 groups.
        Deduplicating FIRST-wins, which is what the plan specified, keeps 0 of the 151."""
        import json as _json, shutil as _sh, tempfile as _tf
        d = _tf.mkdtemp(prefix="hl-gem4-")
        self.addCleanup(_sh.rmtree, d, True)
        p = os.path.join(d, "x.jsonl")
        lines = list(open(GEMFIX, encoding="utf-8"))
        at = [i for i, ln in enumerate(lines) if '"id":"gm-2"' in ln.replace(" ", "")]
        self.assertEqual(len(at), 1)
        bare = _json.loads(lines[at[0]])
        bare.pop("toolCalls")
        lines.insert(at[0], _json.dumps(bare) + "\n")   # the text-only first emission
        open(p, "w", encoding="utf-8").writelines(lines)
        events = self.mod.parse_file(p).events
        calls = [e for e in events if e.kind == "tool_call"]
        self.assertEqual([e.call_id for e in calls], ["gc-1"],
                         "the re-emission carrying the tool calls must win over the bare first one")
        ids = [e.record_id.split(":", 1)[0] for e in events if e.record_id]
        self.assertEqual(ids.count("gm-2"), 4)          # reasoning, assistant, call, result: once
        self.assertLess(ids.index("gm-2"), ids.index("gm-4"))

    def test_a_prompt_only_session_is_still_claimed(self):
        """7 of the operator's 31 real sessions are a header, one `user` row and metadata-only
        `$set` envelopes -- a prompt with no model reply. They hold no `gemini` row, no `toolCalls`
        and no `tokens`, so the content-derived sniff terms score them 0.15 and the adapter walks
        past 23% of the corpus. The header row alone has to carry them."""
        import json as _json, shutil as _sh, tempfile as _tf
        d = _tf.mkdtemp(prefix="hl-gem5-")
        self.addCleanup(_sh.rmtree, d, True)
        p = os.path.join(d, "x.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            for r in [{"kind": "main", "sessionId": "gs-9", "projectHash": "abc123",
                       "startTime": "2026-09-09T12:00:00.000Z",
                       "lastUpdated": "2026-09-09T12:00:02.000Z"},
                      {"type": "user", "id": "gm-9", "timestamp": "2026-09-09T12:00:01.000Z",
                       "content": [{"text": "are you there?"}]},
                      {"$set": {"lastUpdated": "2026-09-09T12:00:02.000Z"}}]:
                f.write(_json.dumps(r) + "\n")
        self.assertGreaterEqual(self.mod.sniff(p), 0.5)
        self.assertEqual([e.kind for e in self.mod.parse_file(p).events], ["user"])


    def test_the_diff_stat_becomes_countable_hunks(self):
        raw = [e for e in self.sess.events if e.kind == "tool_result"][0].raw or {}
        hunks = raw["structured_patch"]
        self.assertEqual(sum(1 for h in hunks for l in h["lines"] if l.startswith("+")), 1)
        self.assertEqual(raw["file_path"], "/srv/calcrepo/calc.py")



class TestCodexIdsAndUsage(unittest.TestCase):
    """Codex records token usage in `event_msg/token_count`, which codex.py dropped wholesale --
    so every captured Codex run showed zero input tokens, an estimated output count, and a cost
    computed from len(text)//4."""

    @classmethod
    def setUpClass(cls):
        from harnesslab.backend.importers import codex
        cls.codex = codex
        cls.sess = codex.parse_file(CXFIX)

    def tmpdir(self):
        d = tempfile.mkdtemp(prefix="hl-codex-")
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def test_every_conversational_event_carries_a_record_id(self):
        conv = [e for e in self.sess.events
                if e.kind in ("user", "assistant", "reasoning", "tool_call", "tool_result")]
        self.assertTrue(conv)
        self.assertTrue(all(e.record_id for e in conv))

    def test_record_ids_are_namespaced_by_session_so_two_rollouts_never_collide(self):
        """Codex ordinals restart at 1 in every file. A bare ordinal would make two unrelated
        sessions share a uuid set, and identity.relations would report a false supersession."""
        for e in self.sess.events:
            if e.record_id:
                self.assertTrue(e.record_id.startswith(self.sess.session_id + "#"), e.record_id)

    def test_a_rollout_without_ordinals_falls_back_to_the_file_line_index(self):
        """`ordinal` is present in Sept-2026 rollouts and absent in Apr/May/Jun/Jul ones on this
        same machine, so the fallback is load-bearing, not defensive."""
        src = os.path.join(self.tmpdir(), "old.jsonl")
        with open(CXFIX, encoding="utf-8") as f, open(src, "w", encoding="utf-8") as out:
            for line in f:
                rec = json.loads(line)
                rec.pop("ordinal", None)
                out.write(json.dumps(rec) + "\n")
        s = self.codex.parse_file(src)
        ids = [e.record_id for e in s.events if e.record_id]
        self.assertTrue(ids)
        self.assertEqual(len(ids), len(set(ids)))

    def test_usage_is_read_from_token_count(self):
        used = [e.usage for e in self.sess.events if e.usage]
        self.assertEqual(len(used), 4)
        self.assertEqual(used[0], {"input_tokens": 1840, "output_tokens": 96,
                                   "cache_read_input_tokens": 1600,
                                   "cache_creation_input_tokens": 240})

    def test_the_cumulative_total_is_never_used_because_it_would_double_count(self):
        self.assertEqual(sum(u["input_tokens"] for u in
                             (e.usage for e in self.sess.events if e.usage)),
                         1840 + 2100 + 2400 + 2600)

    def test_one_request_id_per_token_count_boundary(self):
        rids = [e.request_id for e in self.sess.events if e.request_id]
        self.assertEqual(len(set(rids)), 4)      # one per token_count boundary

    def test_conversational_uuids_are_now_real_for_codex(self):
        """Without record ids, identity.conv_uuids returns [] and the resume/fork graph is empty
        for every Codex run (identity.py:41-42, :121)."""
        from harnesslab.capture import identity
        self.assertTrue(identity.conv_uuids(self.sess.events))

    def rewrite(self, name, fn):
        """A copy of the fixture with every record passed through `fn`, which may return None to
        drop it. Every generation test below is one of these."""
        src = os.path.join(self.tmpdir(), name)
        rows = _cx_rows()
        out = [r for r in (fn(dict(r)) for r in rows) if r is not None]
        with open(src, "w", encoding="utf-8") as f:
            for r in out:
                f.write(json.dumps(r) + "\n")
        return src

    def test_tool_results_carry_an_authoritative_status(self):
        res = [e for e in self.sess.events if e.kind == "tool_result"]
        self.assertEqual([e.call_id for e in res], ["call_aa1", "call_aa2", "call_aa3"])
        # call_aa2's window holds a FileChange, which reports `status` and no exit code; it
        # completed, so it is True. Nothing here is inferred from output text.
        self.assertEqual([e.ok for e in res], [True, True, True])

    def test_a_nonzero_exit_code_makes_the_result_not_ok_and_keeps_the_stderr(self):
        def fail(r):
            item = (r.get("payload") or {}).get("item") or {}
            if item.get("id", "").startswith("exec-aa3"):
                item["exit_code"] = 1
                item["status"] = "failed"
                item["stderr"] = "1 failed in 0.04s"
            return r

        s = self.codex.parse_file(self.rewrite("fail.jsonl", fail))
        last = [e for e in s.events if e.kind == "tool_result"][-1]
        self.assertIs(last.ok, False)
        self.assertEqual((last.raw or {}).get("stderr"), "1 failed in 0.04s")

    def test_a_status_of_failed_without_an_exit_code_is_still_not_ok(self):
        def fail(r):
            item = (r.get("payload") or {}).get("item") or {}
            if item.get("id", "").startswith("exec-aa1"):
                item.pop("exit_code", None)
                item["status"] = "failed"
            return r

        s = self.codex.parse_file(self.rewrite("statusonly.jsonl", fail))
        self.assertIs([e for e in s.events if e.kind == "tool_result"][0].ok, False)

    def test_an_item_that_falls_outside_every_window_is_attributed_to_nothing(self):
        """The honest half of a positional join. An item that arrives after its call's output
        belongs to no open call, so `ok` stays None rather than landing on a neighbour."""
        def move(r):
            p = r.get("payload") or {}
            if (p.get("item") or {}).get("id", "").startswith("exec-aa1"):
                r["ordinal"] = 999          # re-ordered below
            return r

        rows = _cx_rows()
        item = [r for r in rows if ((r.get("payload") or {}).get("item") or {})
                .get("id", "").startswith("exec-aa1")][0]
        rows = [r for r in rows if r is not item] + [item]      # after everything, window closed
        src = os.path.join(self.tmpdir(), "orphan.jsonl")
        with open(src, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        s = self.codex.parse_file(src)
        self.assertIsNone([e for e in s.events if e.kind == "tool_result"][0].ok)

    def test_two_items_in_one_window_both_count_and_a_failure_wins(self):
        """5,993 real windows hold two or more items: one call, a persistent shell, several
        commands. One failure among them is a failed tool call."""
        rows = _cx_rows()
        first = [r for r in rows if ((r.get("payload") or {}).get("item") or {})
                 .get("id", "").startswith("exec-aa1")][0]
        second = json.loads(json.dumps(first))
        second["payload"]["item"]["id"] = "exec-aa1-2-1-1-deadbeef"
        second["payload"]["item"]["exit_code"] = 2
        second["payload"]["item"]["status"] = "failed"
        rows.insert(rows.index(first) + 1, second)
        src = os.path.join(self.tmpdir(), "two.jsonl")
        with open(src, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        s = self.codex.parse_file(src)
        self.assertIs([e for e in s.events if e.kind == "tool_result"][0].ok, False)

    def test_the_2026_04_generation_joins_on_the_call_id_it_actually_carries(self):
        """gen B emits exec_command_end / patch_apply_end, each naming its call_id -- 16,843
        records on this machine, every one of which resolves. No window is involved."""
        rows = _cx_rows()
        out, pending = [], None
        for r in rows:
            p = r.get("payload") or {}
            if p.get("type") == "custom_tool_call":
                pending = p.get("call_id")
            if p.get("type") != "item_completed":
                out.append(r)
                continue
            item = p.get("item") or {}
            if item.get("type") == "CommandExecution":
                out.append({"timestamp": r["timestamp"], "type": "event_msg",
                            "payload": {"type": "exec_command_end", "call_id": pending,
                                        "turn_id": "turn_1", "command": item["command"],
                                        "cwd": item["cwd"], "exit_code": item["exit_code"],
                                        "status": item["status"], "stdout": item["stdout"],
                                        "stderr": item["stderr"],
                                        "aggregated_output": item["aggregated_output"],
                                        "formatted_output": item["formatted_output"],
                                        "parsed_cmd": item["parsed_cmd"],
                                        "duration": item["duration"],
                                        "process_id": item["process_id"],
                                        "source": item["source"]}})
            else:
                out.append({"timestamp": r["timestamp"], "type": "event_msg",
                            "payload": {"type": "patch_apply_end", "call_id": pending,
                                        "turn_id": "turn_1", "changes": item["changes"],
                                        "status": item["status"], "success": True,
                                        "stdout": item["stdout"], "stderr": item["stderr"]}})
        src = os.path.join(self.tmpdir(), "gen_b.jsonl")
        with open(src, "w", encoding="utf-8") as f:
            for r in out:
                f.write(json.dumps(r) + "\n")
        s = self.codex.parse_file(src)
        self.assertEqual([e.ok for e in s.events if e.kind == "tool_result"], [True, True, True])

    def test_a_rollout_with_no_status_source_leaves_ok_unknown_rather_than_guessing(self):
        """gen A (2026-01..03 and 2026-07) has no exit code anywhere. `ok` must stay None: a
        guessed pass is worse than an admitted unknown, and writer.py's own heuristic is the
        fallback either way."""
        def drop(r):
            return None if (r.get("payload") or {}).get("type") == "item_completed" else r

        s = self.codex.parse_file(self.rewrite("gen_a.jsonl", drop))
        res = [e for e in s.events if e.kind == "tool_result"]
        self.assertEqual(len(res), 3)
        self.assertEqual([e.ok for e in res], [None, None, None])

    def test_the_patch_call_learns_its_real_path_and_becomes_an_edit(self):
        """In the real corpus the patch is run THROUGH the shell: 40,260 `exec` calls against one
        literally-named `apply_patch`. Only the FileChange record in the call's window says files
        changed, so without the re-name the edit is a bash span and the ledger shows no edits."""
        call = [e for e in self.sess.events
                if e.kind == "tool_call" and e.call_id == "call_aa2"][0]
        self.assertEqual(call.args.get("file_path"), "/srv/calcrepo/calc.py")
        self.assertEqual(call.name, "apply_patch")
        self.assertEqual(call.args.get("native_name"), "exec",
                         "the native tool name is preserved, not discarded")
        from harnesslab.backend.importers import common as C
        tool, args = C.map_tool(call.name, call.args, self.sess.extra["tool_overrides"])
        self.assertEqual(tool, "edit_file")
        self.assertEqual(args["path"], "/srv/calcrepo/calc.py")

    def test_the_policy_strategy_names_the_boundary_codex_actually_recorded(self):
        """Over all 1,423 rollouts on this machine approval_policy is one of {on-request, never}
        and sandbox_policy.type one of {workspace-write, danger-full-access, read-only}. The
        fixture is on-request + workspace-write: a real boundary, so `strict`."""
        from harnesslab.capture.harness_config import POLICIES
        self.assertEqual(POLICIES["codex_approval"](self.sess),
                         ("strict", {"approval_policy": "on-request",
                                     "sandbox_policy": "workspace-write"}))

    def test_a_removed_boundary_is_permissive_and_an_unrecorded_one_is_unknown(self):
        """`permissive` only when a boundary was OBSERVED removed -- never inferred from silence."""
        from harnesslab.capture.harness_config import POLICIES

        def run(approval, sandbox):
            def two(r):
                if r.get("type") == "turn_context":
                    r["payload"]["approval_policy"] = approval
                    r["payload"]["sandbox_policy"] = {"type": sandbox}
                return r
            s = self.codex.parse_file(self.rewrite(f"pol-{approval}-{sandbox}.jsonl", two))
            return POLICIES["codex_approval"](s)[0]

        self.assertEqual(run("never", "workspace-write"), "permissive")
        self.assertEqual(run("on-request", "danger-full-access"), "permissive")
        self.assertEqual(run("on-request", "read-only"), "strict")

        def strip(r):
            if r.get("type") == "turn_context":
                r["payload"].pop("approval_policy", None)
                r["payload"].pop("sandbox_policy", None)
            return r
        s = self.codex.parse_file(self.rewrite("nopolicy.jsonl", strip))
        self.assertEqual(POLICIES["codex_approval"](s), ("unknown", {}))

    def test_the_ledger_reports_the_tool_that_was_really_invoked(self):
        """The re-name to apply_patch is the only way an `edit` span exists for Codex at all, but
        the ledger's `native_tool` must still say `exec` -- otherwise every Codex run on this
        machine asserts a tool call that never happened (40,260 exec against one apply_patch)."""
        from harnesslab.capture import regen
        out = self.tmpdir()
        with mock.patch.dict(regen.ADAPTERS, {"codex": self.codex}):
            runs = regen.regenerate(CXFIX, out, 1800, adapter="codex")
        with open(os.path.join(out, runs[0].run_id, "ledger.jsonl"), encoding="utf-8") as f:
            spans = [json.loads(line) for line in f]
        tools = [(s["gen_ai.tool.name"], s["native_tool"])
                 for s in spans if s["span"] == "execute_tool"]
        self.assertEqual(tools, [("bash", "exec"), ("edit_file", "exec"), ("bash", "exec")])
        self.assertEqual([s["path"] for s in spans if s["span"] == "edit"],
                         ["/srv/calcrepo/calc.py"])

    def test_no_chat_span_has_to_estimate_its_usage(self):
        """Measured on the newest 200 real rollouts: where both exist, the last token_count comes
        after the last assistant message 117 times out of 117. So a Codex run has a usage record
        for every model call, and an estimated chat span means the join dropped one."""
        from harnesslab.capture import regen
        out = self.tmpdir()
        with mock.patch.dict(regen.ADAPTERS, {"codex": self.codex}):
            runs = regen.regenerate(CXFIX, out, 1800, adapter="codex")
        with open(os.path.join(out, runs[0].run_id, "ledger.jsonl"), encoding="utf-8") as f:
            spans = [json.loads(line) for line in f]
        chats = [s for s in spans if s["span"] == "chat"]
        self.assertEqual(len(chats), 4)
        self.assertEqual([s["usage_estimated"] for s in chats], [False] * 4)
        self.assertEqual([s["gen_ai.usage.input_tokens"] for s in chats],
                         [1840, 2100, 2400, 2600])

    def test_a_call_that_changed_nothing_keeps_its_native_name(self):
        for cid in ("call_aa1", "call_aa3"):
            call = [e for e in self.sess.events
                    if e.kind == "tool_call" and e.call_id == cid][0]
            self.assertEqual(call.name, "exec")
            self.assertNotIn("file_path", call.args)

    def test_the_result_carries_hunks_the_writer_can_count_lines_from(self):
        res = [e for e in self.sess.events
               if e.kind == "tool_result" and e.call_id == "call_aa2"][0]
        hunks = (res.raw or {}).get("structured_patch")
        self.assertTrue(hunks)
        added = sum(1 for h in hunks for l in h["lines"] if l.startswith("+"))
        removed = sum(1 for h in hunks for l in h["lines"] if l.startswith("-"))
        self.assertEqual((added, removed), (1, 1))
        self.assertEqual(hunks[0]["oldStart"], 1)
        self.assertEqual(hunks[0]["newLines"], 2)

    def test_the_header_lines_of_a_unified_diff_are_not_counted_as_edits(self):
        """--- and +++ start with - and + and would be counted as a removed and an added line
        if the hunk parser did not require an open @@ header first."""
        hunks = (
            [e for e in self.sess.events
             if e.kind == "tool_result" and e.call_id == "call_aa2"][0].raw or {}
        )["structured_patch"]
        self.assertFalse(any(l.startswith("---") or l.startswith("+++")
                             for h in hunks for l in h["lines"]))

    def test_the_session_patch_is_the_sources_own_unified_diff(self):
        self.assertIn("--- a/calc.py", self.sess.patch)
        self.assertIn("+    return a + b", self.sess.patch)

    def test_a_multi_file_change_reports_every_file_in_the_patch(self):
        def two(r):
            item = (r.get("payload") or {}).get("item") or {}
            if item.get("type") == "FileChange":
                item["changes"]["/srv/calcrepo/util.py"] = {
                    "type": "update", "move_path": None,
                    "unified_diff": "--- a/util.py\n+++ b/util.py\n@@ -1,1 +1,1 @@\n-x = 1\n+x = 2\n"}
            return r

        s = self.codex.parse_file(self.rewrite("twofiles.jsonl", two))
        self.assertIn("util.py", s.patch)
        self.assertIn("calc.py", s.patch)

    def test_a_content_only_change_has_no_diff_and_is_not_invented(self):
        """1,837 of 10,537 real change entries are `{type, content}` with no unified_diff -- a file
        written whole. The path is real and the hunks are empty; nothing is synthesised."""
        def whole(r):
            item = (r.get("payload") or {}).get("item") or {}
            if item.get("type") == "FileChange":
                item["changes"] = {"/srv/calcrepo/new.py": {"type": "add", "content": "x = 1\n"}}
            return r

        s = self.codex.parse_file(self.rewrite("whole.jsonl", whole))
        res = [e for e in s.events if e.kind == "tool_result" and e.call_id == "call_aa2"][0]
        self.assertEqual((res.raw or {})["file_path"], "/srv/calcrepo/new.py")
        self.assertEqual((res.raw or {})["structured_patch"], [])

    def test_the_system_prompt_is_read_from_base_instructions(self):
        from harnesslab.backend.importers import common as C
        self.assertTrue(self.sess.system_prompt)
        fp = C.fingerprint(self.sess.events,
                           {"source": "codex", "system_prompt": self.sess.system_prompt})
        self.assertTrue(fp["system_prompt_sha256"])
        self.assertGreater(fp["system_prompt_chars"], 0)

    def test_the_pre_2026_08_instructions_field_is_still_read(self):
        src = os.path.join(self.tmpdir(), "old_field.jsonl")
        rows = _cx_rows()
        rows[0]["payload"].pop("base_instructions", None)
        rows[0]["payload"]["instructions"] = "Older Codex build prompt."
        with open(src, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        self.assertEqual(self.codex.parse_file(src).system_prompt, "Older Codex build prompt.")

    def test_the_sandbox_policy_is_a_value_not_a_python_repr(self):
        self.assertEqual(self.sess.extra["sandbox_policy"], "workspace-write")
        self.assertNotIn("{", self.sess.extra["policy_note"])
        self.assertIn("sandbox=workspace-write", self.sess.extra["policy_note"])

    def test_dropped_record_types_are_counted_not_silently_discarded(self):
        src = os.path.join(self.tmpdir(), "weird.jsonl")
        lines = _cx_lines()
        lines.append(json.dumps({"type": "world_state", "ordinal": 99, "payload": {}}) + "\n")
        lines.append(json.dumps({"type": "event_msg", "ordinal": 100,
                                 "payload": {"type": "turn_aborted"}}) + "\n")
        with open(src, "w", encoding="utf-8") as f:
            f.writelines(lines)
        got = self.codex.parse_file(src).extra["unknown_record_types"]
        self.assertEqual(got, {"world_state": 1, "event_msg/turn_aborted": 1})

    def test_the_fixture_itself_leaves_no_unknown_record_types(self):
        """Conformance check 6 bites once counting exists: every event_msg subtype the fixture
        contains must be HANDLED, not merely counted."""
        self.assertEqual(self.sess.extra["unknown_record_types"], {})

    def test_exec_maps_to_bash_without_a_glued_on_prefix(self):
        from harnesslab.backend.importers import common as C
        tool, args = C.map_tool("exec", {"input": "cat calc.py"},
                                self.sess.extra["tool_overrides"])
        self.assertEqual(tool, "bash")
        self.assertEqual(args["command"], "cat calc.py")

    def test_native_lineage_is_recorded_rather_than_reconstructed(self):
        """Codex writes forked_from_id / parent_thread_id. Ordinals restart per file, so
        uuid containment would invent relations that are not there."""
        src = os.path.join(self.tmpdir(), "forked.jsonl")
        rows = _cx_rows()
        rows[0]["payload"]["forked_from_id"] = "01936a4c-0000-0000-0000-00000000dead"
        rows[0]["payload"]["id"] = "01936a4c-1111-2222-3333-000000000002"
        with open(src, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        self.assertEqual(self.codex.parse_file(src).extra["parent_session_id"],
                         "01936a4c-0000-0000-0000-00000000dead")

    def test_two_rollouts_of_different_sessions_declare_no_relation(self):
        from harnesslab.capture import identity
        src = os.path.join(self.tmpdir(), "other.jsonl")
        rows = _cx_rows()
        rows[0]["payload"]["id"] = "01936a4c-1111-2222-3333-000000000009"
        with open(src, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        a = identity.conv_uuids(self.sess.events)
        b = identity.conv_uuids(self.codex.parse_file(src).events)
        self.assertTrue(a and b)
        self.assertEqual(set(a) & set(b), set())
        self.assertEqual(identity.relations({"run_a": a, "run_b": b}), {})


# --------------------------------------------------------------------------- qwen code
QWFIX = os.path.join(LAB, "tests_agentlab", "fixtures", "capture", "qwen_chat.jsonl")


class TestQwenCode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from harnesslab.backend.importers import qwen_code
        cls.mod = qwen_code
        cls.sess = qwen_code.parse_file(QWFIX)

    def test_it_parses_at_all_which_claude_code_never_did(self):
        self.assertIsNone(claude_code.parse_file(QWFIX))
        self.assertIsNotNone(self.sess)
        self.assertTrue(self.sess.events)

    def test_the_envelope_uuid_is_the_record_id_so_dedup_and_relations_work(self):
        from harnesslab.capture import identity
        self.assertTrue(all(e.record_id for e in self.sess.events
                            if e.kind in ("user", "assistant", "reasoning",
                                          "tool_call", "tool_result")))
        self.assertTrue(identity.conv_uuids(self.sess.events))

    def test_usage_comes_from_usage_metadata(self):
        used = [e.usage for e in self.sess.events if e.usage]
        self.assertEqual(used[0], {"input_tokens": 1200, "output_tokens": 80,
                                   "cache_read_input_tokens": 900,
                                   "cache_creation_input_tokens": 0})

    def test_a_thought_flagged_text_part_is_reasoning_and_a_plain_one_is_assistant(self):
        """`thought` is a BOOLEAN flag on a text part, measured 2,613/2,613 on the real corpus.
        An adapter reading it as the reasoning STRING emits no reasoning at all and relabels
        every thinking block as assistant prose."""
        kinds = [e.kind for e in self.sess.events]
        self.assertIn("reasoning", kinds)
        self.assertIn("assistant", kinds)
        reasoning = [e for e in self.sess.events if e.kind == "reasoning"]
        self.assertEqual([e.text for e in reasoning], ["Read then patch."])

    def test_the_model_is_read_from_the_row_not_hardcoded(self):
        """Five distinct model strings live in the real corpus; (model, harness) is the unit of
        compare, so a hardcoded 'qwen' would collapse all of them into one."""
        self.assertEqual(self.sess.model, "qwen3.8-max")
        self.assertEqual({e.model for e in self.sess.events if e.model}, {"qwen3.8-max"})

    def test_the_tool_result_status_is_authoritative_and_carries_the_diff_counts(self):
        res = [e for e in self.sess.events if e.kind == "tool_result"]
        self.assertEqual([(e.call_id, e.ok) for e in res], [("qc-1", True)])
        hunks = (res[0].raw or {}).get("structured_patch")
        added = sum(1 for h in hunks for l in h["lines"] if l.startswith("+"))
        removed = sum(1 for h in hunks for l in h["lines"] if l.startswith("-"))
        self.assertEqual((added, removed), (1, 1))

    def test_the_edited_path_comes_from_inside_result_display(self):
        """`toolCallResult.filePath` does not exist -- 0 of 3,098 real results carry it. The path
        is `resultDisplay.filePath` / `.fileName`."""
        res = [e for e in self.sess.events if e.kind == "tool_result"]
        self.assertEqual((res[0].raw or {}).get("file_path"), "/srv/calcrepo/calc.py")

    def test_transport_system_rows_are_not_counted_as_unknown(self):
        self.assertEqual(self.sess.extra["unknown_record_types"], {})

    def test_an_unrecognised_system_subtype_is_counted(self):
        import json as _json, shutil as _sh, tempfile as _tf
        d = _tf.mkdtemp(prefix="hl-qwen-")
        self.addCleanup(_sh.rmtree, d, True)
        p = os.path.join(d, "x.jsonl")
        lines = list(open(QWFIX, encoding="utf-8"))
        lines.append(_json.dumps({"type": "system", "uuid": "qsys-9", "sessionId": "qs-1",
                                  "provenance": "system", "version": "0.23.3",
                                  "subtype": "brand_new_thing",
                                  "systemPayload": {"uiEvent": {}}}) + "\n")
        open(p, "w", encoding="utf-8").writelines(lines)
        self.assertEqual(self.mod.parse_file(p).extra["unknown_record_types"],
                         {"system/brand_new_thing": 1})

    def test_the_subtype_is_read_from_the_top_level_where_the_corpus_puts_it(self):
        """14,220 of 14,220 real system rows carry `subtype` at the top level and none carries
        `systemPayload.subtype`. Reading the nested one puts ~70% of every chat file into
        unknown_record_types -- silently, because a counted unknown is not an error."""
        import json as _json, shutil as _sh, tempfile as _tf
        d = _tf.mkdtemp(prefix="hl-qwen-sub-")
        self.addCleanup(_sh.rmtree, d, True)
        p = os.path.join(d, "x.jsonl")
        rows = [_json.loads(l) for l in open(QWFIX, encoding="utf-8")]
        for r in rows:
            if r.get("type") == "system":
                self.assertIn("subtype", r, "the fixture must match the corpus, not the adapter")
                self.assertNotIn("subtype", r.get("systemPayload") or {})
        open(p, "w", encoding="utf-8").writelines(_json.dumps(r) + "\n" for r in rows)
        self.assertEqual(self.mod.parse_file(p).extra["unknown_record_types"], {})

    def test_the_policy_decision_is_read_from_the_ui_event(self):
        self.assertEqual(self.sess.extra["decisions"], ["auto_accept"])

    def test_the_approval_decision_drives_the_policy(self):
        from harnesslab.capture.harness_config import POLICIES
        self.assertEqual(POLICIES["qwen_decision"](self.sess),
                         ("permissive", {"decisions": ["auto_accept"]}))

    def test_a_session_with_no_observed_decision_is_unknown_not_a_guess(self):
        from harnesslab.capture.harness_config import POLICIES

        class _Sess:
            extra = {"decisions": []}
        self.assertEqual(POLICIES["qwen_decision"](_Sess()), ("unknown", {}))

    def test_the_policy_reaches_capture_harness_once_the_descriptor_is_registered(self):
        """The descriptor lands in adapters.REGISTRY in its own change; this proves the rest of
        the chain -- BY_NAME -> descriptor.policy -> POLICIES -> capture_harness."""
        from unittest import mock as _mock
        from harnesslab.capture import adapters, harness_config
        row = adapters.CaptureAdapter(
            name="qwen_code", module=self.mod, description="Qwen Code chats",
            fixture="qwen_chat.jsonl", golden="qwen_chat.expected.ledger.jsonl",
            policy="qwen_decision")
        with _mock.patch.dict(adapters.BY_NAME, {"qwen_code": row}):
            h = harness_config.capture_harness(self.sess)
        self.assertEqual(h["policy"], "permissive")
        self.assertEqual(h["observed_config"]["decisions"], ["auto_accept"])


if __name__ == "__main__":
    unittest.main()


class TestCodexJoinDoesNotDependOnRecordOrder(unittest.TestCase):
    """The gen-C item carries no call_id, so it is attributed positionally -- and the module's own
    comment says the item and the matching `_output` "arrive in either order depending on the build".

    Attribution used the stack of calls still AWAITING output, so on an output-first build the call
    had already been popped: every `ok` reverted to None and the FileChange was dropped, taking the
    session's only edit with it. The window is anchored on the most recently OPENED call instead,
    which both orderings agree on.
    """

    FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "capture", "cx_rollout.jsonl")

    @staticmethod
    def _kind(r):
        pl = r.get("payload") or r
        return pl.get("type") or r.get("type") or ""

    def _measure(self, rows):
        from harnesslab.backend.importers import codex
        d = tempfile.mkdtemp(prefix="hl-cxorder-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "rollout-probe.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        sess = codex.parse_file(path)
        self.assertIsNotNone(sess)
        oks = [e.ok for e in sess.events if e.kind == "tool_result"]
        return sum(o is not None for o in oks), len(sess.patch or "")

    def test_an_output_first_build_still_joins_status_and_edits(self):
        rows = [json.loads(l) for l in open(self.FIX, encoding="utf-8") if l.strip()]
        shipped_ok, shipped_patch = self._measure(rows)
        self.assertGreater(shipped_ok, 0, "the shipped fixture should join")
        self.assertGreater(shipped_patch, 0)

        # The fixture is call, item, output. Emit the OUTPUT first -- call, output, item -- which is
        # what the other build does. (Swapping with the preceding record instead puts the item ahead
        # of any call, which is a different and far less interesting shape.)
        swapped = list(rows)
        for i in range(len(swapped) - 1):
            if (self._kind(swapped[i]) == "item_completed"
                    and self._kind(swapped[i + 1]).endswith("_output")):
                swapped[i], swapped[i + 1] = swapped[i + 1], swapped[i]
        ok, patch = self._measure(swapped)
        self.assertEqual(ok, shipped_ok, "record order changed how many results carry a status")
        self.assertEqual(patch, shipped_patch, "record order changed the captured edits")


class TestCodexRecordIdsDoNotCollide(unittest.TestCase):
    """`ordinal` and the line-number fallback are two numbering schemes, and one file uses both.

    Unnamespaced they collide on any build whose ordinals are not 1-based, and the collision is
    silent: identity.dedup_events keeps one record and drops the other, taking a tool call with it.
    """

    FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "capture", "cx_rollout.jsonl")

    def test_a_zero_based_build_does_not_lose_a_record_to_a_collision(self):
        from harnesslab.backend.importers import codex
        rows = [json.loads(l) for l in open(self.FIX, encoding="utf-8") if l.strip()]
        shifted = []
        for r in rows:                              # a 0-based build: every ordinal one lower
            r = dict(r)
            if isinstance(r.get("ordinal"), int):
                r["ordinal"] = r["ordinal"] - 1
            shifted.append(r)

        d = tempfile.mkdtemp(prefix="hl-cxid-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "rollout-zero.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for r in shifted:
                f.write(json.dumps(r) + "\n")

        sess = codex.parse_file(path)
        self.assertIsNotNone(sess)
        ids = [e.record_id for e in sess.events if e.record_id]
        self.assertEqual(len(ids), len(set(ids)), "two records share a record_id")
        base = codex.parse_file(self.FIX)
        self.assertEqual(len([e for e in sess.events if e.kind == "tool_call"]),
                         len([e for e in base.events if e.kind == "tool_call"]),
                         "a tool call was lost to an id collision")


class TestGeminiSetIsAPatchNotAReplacement(unittest.TestCase):
    """`$set` is a patch envelope. Collapsing by id kept the LAST version wholesale, which is right
    when Gemini rewrites a message to add its tool calls and wrong when the envelope carries only
    metadata: a tokens-only entry then destroyed the text, the thoughts, the tool call and its
    result -- the session's only edit among them.
    """

    FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "capture", "gemini_session.jsonl")

    def _parse(self, rows):
        from harnesslab.backend.importers import gemini_cli
        d = tempfile.mkdtemp(prefix="hl-gmset-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "session.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        return gemini_cli.parse_file(path)

    def test_a_metadata_only_set_does_not_delete_the_message_it_patches(self):
        rows = [json.loads(l) for l in open(self.FIX, encoding="utf-8") if l.strip()]
        base = self._parse(rows)
        self.assertIsNotNone(base)
        target = next((str(r.get("id")) for r in rows
                       if r.get("type") == "gemini" and r.get("id")), None)
        self.assertIsNotNone(target, "fixture should carry an assistant message with an id")

        patched = rows + [{"$set": {"messages": [{"id": target, "tokens": {"input": 7, "output": 3}}]}}]
        after = self._parse(patched)
        self.assertIsNotNone(after)
        self.assertEqual(len(after.events), len(base.events),
                         "a tokens-only $set destroyed the record it was patching")
        self.assertEqual([e.kind for e in after.events], [e.kind for e in base.events])
