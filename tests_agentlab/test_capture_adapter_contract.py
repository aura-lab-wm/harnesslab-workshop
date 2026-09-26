"""One contract, four authors.

The conformance kit (test_capture_conformance) proves an adapter produces the right LEDGER.
It cannot see the thing that has actually bitten this project: four people writing four
adapters in parallel, each assuming a slightly different interface, and nobody noticing until
a fifth adapter lands on whichever assumption happened to be wrong.

So this file asserts the INTERFACE rather than the output -- the module surface every
descriptor's `module` must present, the key sets the adapters have to agree on, and the
boundary between them. It walks adapters.REGISTRY, so a new descriptor is covered the moment
it is registered and there is no list here to forget to update.
"""
import ast
import inspect
import os
import sys
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

from harnesslab.backend import importers as I                  # noqa: E402
from harnesslab.backend.importers import common as C           # noqa: E402
from harnesslab.capture import adapters, harness_config        # noqa: E402

#: the usage dict every adapter emits, priced by writer.write_run. A fifth key would be dropped
#: silently; a missing one would be read as a zero-token call.
USAGE_KEYS = {"input_tokens", "output_tokens",
              "cache_read_input_tokens", "cache_creation_input_tokens"}


class TestAdapterModuleSurface(unittest.TestCase):
    def test_every_descriptor_names_its_own_module(self):
        for ad in adapters.REGISTRY:
            with self.subTest(adapter=ad.name):
                self.assertEqual(ad.module.NAME, ad.name,
                                 "descriptor name and module NAME must agree: capture_harness "
                                 "looks the descriptor up by sess.source, which is module.NAME")

    def test_every_adapter_presents_the_same_three_callables(self):
        for ad in adapters.REGISTRY:
            with self.subTest(adapter=ad.name):
                for fn, params in (("sniff", ["path"]), ("parse_file", ["path"]),
                                   ("sessions", ["path"])):
                    f = getattr(ad.module, fn, None)
                    self.assertTrue(callable(f), f"{ad.name} has no {fn}()")
                    got = list(inspect.signature(f).parameters)
                    self.assertEqual(got[:1], params,
                                     f"{ad.name}.{fn} takes {got}, the registry calls it with "
                                     f"one positional path")

    def test_every_adapter_describes_itself_for_the_import_page(self):
        for ad in adapters.REGISTRY:
            with self.subTest(adapter=ad.name):
                self.assertTrue(getattr(ad.module, "DESCRIPTION", ""))
                self.assertTrue(list(getattr(ad.module, "PATTERNS", [])))

    def test_every_capture_adapter_is_also_an_import_source(self):
        """The Capture page and the Import page must offer the same set of harnesses. A source
        capturable but not importable is a file the user can watch and cannot hand over."""
        importable = {m.NAME for m in I.SOURCES}
        for ad in adapters.REGISTRY:
            with self.subTest(adapter=ad.name):
                self.assertIn(ad.name, importable)

    def test_every_descriptor_names_a_policy_that_exists(self):
        """A descriptor naming a strategy nobody wrote raises KeyError on the FIRST captured
        file -- one error in the report, no runs, and nothing that says why."""
        for ad in adapters.REGISTRY:
            with self.subTest(adapter=ad.name):
                self.assertIn(ad.policy, harness_config.POLICIES)

    def test_every_descriptor_can_actually_be_walked(self):
        for ad in adapters.REGISTRY:
            with self.subTest(adapter=ad.name):
                self.assertTrue(ad.globs, "an empty glob tuple matches nothing")
                self.assertTrue(ad.default_roots,
                                "with no default root the Capture page can offer nothing to allow-list")


class TestAdaptersAgreeOnSharedShapes(unittest.TestCase):
    def test_the_fixtures_agree_on_the_usage_key_set(self):
        for ad in adapters.REGISTRY:
            with self.subTest(adapter=ad.name):
                fx = os.path.join(LAB, "tests_agentlab", "fixtures", "capture", ad.fixture)
                sess = next(iter(ad.module.sessions(fx)))
                seen = [e.usage for e in sess.events if e.usage]
                if not ad.has_usage:
                    self.assertEqual(seen, [], f"{ad.name}: declares no usage but emits some")
                    continue
                self.assertTrue(seen, f"{ad.name}: no event carries usage")
                for u in seen:
                    self.assertEqual(set(u), USAGE_KEYS)

    def test_the_fixtures_agree_on_the_session_source(self):
        for ad in adapters.REGISTRY:
            with self.subTest(adapter=ad.name):
                fx = os.path.join(LAB, "tests_agentlab", "fixtures", "capture", ad.fixture)
                for sess in ad.module.sessions(fx):
                    self.assertEqual(sess.source, ad.name)

    def test_an_adapter_that_overrides_tool_names_parks_them_where_map_tool_looks(self):
        """`tool_overrides` in sess.extra is the ONE place a native tool table is read from
        (common.map_tool, via writer and convert). An adapter that keeps its table anywhere
        else silently sends every native name into the unnamed-tool fallback."""
        for ad in adapters.REGISTRY:
            with self.subTest(adapter=ad.name):
                table = getattr(ad.module, "TOOL_OVERRIDES", None)
                if table is None:
                    continue
                fx = os.path.join(LAB, "tests_agentlab", "fixtures", "capture", ad.fixture)
                sess = next(iter(ad.module.sessions(fx)))
                self.assertEqual(sess.extra.get("tool_overrides"), table)

    def test_every_adapter_counts_what_it_did_not_understand(self):
        """`unknown_record_types` is how a format change surfaces as a number instead of as
        silence. Conformance check 6 reads it off the end span and would pass vacuously against
        an adapter that never set the key at all."""
        for ad in adapters.REGISTRY:
            with self.subTest(adapter=ad.name):
                fx = os.path.join(LAB, "tests_agentlab", "fixtures", "capture", ad.fixture)
                sess = next(iter(ad.module.sessions(fx)))
                self.assertIsInstance(sess.extra.get("unknown_record_types"), dict)


class TestAdaptersDoNotReachIntoEachOther(unittest.TestCase):
    """Shared code belongs in common.py. An adapter that imports a sibling makes the sibling's
    private helpers load-bearing for a format their author never looked at, and a fix for one
    source silently changes another."""

    def test_no_adapter_imports_another_adapter(self):
        names = {ad.module.NAME for ad in adapters.REGISTRY}
        mods = {ad.module.NAME: ad.module.__file__ for ad in adapters.REGISTRY}
        for name, path in sorted(mods.items()):
            with self.subTest(adapter=name):
                with open(path, encoding="utf-8") as f:
                    tree = ast.parse(f.read())
                reached = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.level and node.module:
                        if node.module.split(".")[0] in names - {name}:
                            reached.add(node.module)
                    elif isinstance(node, ast.Import):
                        for a in node.names:
                            if a.name.split(".")[-1] in names - {name}:
                                reached.add(a.name)
                self.assertEqual(reached, set(),
                                 f"{name} imports sibling adapter(s) {sorted(reached)}; move the "
                                 "shared helper into importers/common.py instead")


if __name__ == "__main__":
    unittest.main()
