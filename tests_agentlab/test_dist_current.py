"""The UI the server ships is the UI in the source tree.

`python -m harnesslab` serves harnesslab/frontend/dist/ exactly as committed. An edit under
frontend/src/ without `npm run build` therefore ships the OLD interface, with nothing to say so.
The build stamps the hash of the source it was built from into dist/build-source.json
(vite.config.js, stampSource); this recomputes it with the same file set and framing.
"""
import hashlib
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FE = os.path.join(ROOT, "harnesslab", "frontend")
DIST = os.path.join(FE, "dist")
TEST_FILE = re.compile(r"\.test\.[jt]sx?$")


def source_files() -> list[str]:
    out = []
    for dirpath, _dirs, files in os.walk(os.path.join(FE, "src")):
        for name in files:
            if TEST_FILE.search(name) or name == ".DS_Store":
                continue
            out.append(os.path.relpath(os.path.join(dirpath, name), FE).replace(os.sep, "/"))
    out += ["index.html", "package.json"]
    return sorted(out)


def source_hash() -> dict:
    h = hashlib.sha256()
    files = source_files()
    for rel in files:
        h.update(rel.encode() + b"\0")
        with open(os.path.join(FE, rel), "rb") as f:
            h.update(f.read())
        h.update(b"\0")
    return {"sha256": h.hexdigest(), "files": len(files)}


@unittest.skipUnless(os.path.isdir(os.path.join(FE, "src")), "no frontend source in this copy (a package build)")
class DistIsCurrent(unittest.TestCase):
    def test_dist_was_built_from_this_source(self):
        stamp_path = os.path.join(DIST, "build-source.json")
        self.assertTrue(os.path.isfile(stamp_path),
                        "dist/ has no build-source.json: rebuild with `cd harnesslab/frontend && npm run build`")
        with open(stamp_path) as f:
            stamp = json.load(f)
        self.assertEqual(stamp, source_hash(),
                         "harnesslab/frontend/src changed since dist/ was built, so the server would ship "
                         "the old UI. Rebuild: cd harnesslab/frontend && npm run build")

    def test_index_points_at_assets_that_exist(self):
        with open(os.path.join(DIST, "index.html")) as f:
            html = f.read()
        refs = re.findall(r'(?:src|href)="/?(assets/[^"]+)"', html)
        self.assertTrue(refs, "dist/index.html references no bundle")
        for ref in refs:
            self.assertTrue(os.path.isfile(os.path.join(DIST, ref)), f"dist/index.html points at missing {ref}")


if __name__ == "__main__":
    unittest.main()
