"""Nothing in the console is allowed to be unreachable: no orphan screen, no uncalled route.

Both halves of this were true only by accident before, and both had already rotted. The old page
set under `frontend/src/pages/` and `frontend/src/decks/` stayed on disk for weeks after App.jsx
stopped importing it, and behind it a tail of backend routes kept answering requests that nothing
made any more. Dead UI is cheap to leave lying around and expensive to read: the next person
greps for a screen, finds two versions of it, and has no way to tell which one the browser runs.

So this asserts the two properties that keep the surface honest:

  (a) every `.jsx` under `frontend/src` is reachable from the entry by following imports. The
      entry is `main.jsx` (what index.html loads), which does nothing but mount `App.jsx`; every
      other component has to be pulled in from there, however deep.

  (b) every route registered on the FastAPI app has a caller. A caller is one of four things: the
      reachable part of the shell, a test under `tests_agentlab/`, the CLI, or the static export,
      which prefetches read-only endpoints so the exported HTML answers without a server. A test
      that calls the handler as a function (`X.import_model(...)`) counts too -- it exercises the
      same code and it is how most of the router tests here are written.

The two halves are deliberately chained: (b) only counts calls made from files (a) says are
reachable. A route whose last caller was an orphan screen therefore reads as uncalled, which is
what it is.

This fails on a new unreachable component and on a new uncalled route, which is the point.
"""
import os
import re
import sys
import unittest

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LAB)

SRC = os.path.join(LAB, "harnesslab", "frontend", "src")
TESTS = os.path.join(LAB, "tests_agentlab")

#: Routes with no caller that are nonetheless kept, each with the reason it earns the exemption.
#: Read this as a debt list, not a parking space: an entry that outlives its reason is a bug.
KEPT_WITHOUT_CALLER = {
    # `POST /api/settings/key` was here: the settings drawer that posted it went with the old page
    # set, and the exemption said the shell was being given a home for it. LabKey.jsx is that home,
    # so the exemption is gone rather than kept as a standing excuse.
    # `GET /api/sentinel/export/{name}` was here, kept as the producer of the bundle
    # `POST /api/sentinel/import` reads. The Rig workbench's sentinel view now calls it (export
    # button per model), so it has a caller and the exemption is gone.
}

#: Paths FastAPI registers on its own behalf, plus the SPA catch-all. Not ours to justify.
FRAMEWORK_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc", "/{path:path}"}


# --------------------------------------------------------------------------- the import graph
def _specifiers(text: str) -> list[str]:
    """Every module specifier the file names, from `import x from 's'` and a bare `import 's'`."""
    return re.findall(r"""(?:\bfrom|\bimport)\s*\(?\s*['"]([^'"]+)['"]""", text)


def _resolve(importer: str, spec: str) -> str | None:
    """The file `spec` names when written in `importer`, or None for a bare package import."""
    if not spec.startswith("."):
        return None
    base = os.path.normpath(os.path.join(os.path.dirname(importer), spec))
    for cand in (base, base + ".jsx", base + ".js",
                 os.path.join(base, "index.jsx"), os.path.join(base, "index.js")):
        if os.path.isfile(cand):
            return cand
    return None


def reachable_files() -> set[str]:
    """Every file reachable from the browser entry, as absolute paths."""
    roots = [os.path.join(SRC, "main.jsx"), os.path.join(SRC, "App.jsx")]
    seen, queue = set(), [r for r in roots if os.path.isfile(r)]
    while queue:
        cur = queue.pop()
        if cur in seen:
            continue
        seen.add(cur)
        if not cur.endswith((".js", ".jsx")):
            continue
        with open(cur, encoding="utf-8") as fh:
            text = fh.read()
        for spec in _specifiers(text):
            nxt = _resolve(cur, spec)
            if nxt and nxt not in seen:
                queue.append(nxt)
    return seen


def all_jsx() -> set[str]:
    """Every component the browser could load -- which is not every .jsx on disk.

    A `*.test.jsx` is imported by the test runner and by nothing else, on purpose. Counting one as an
    orphan makes this guard fail the moment somebody writes a component test, which is backwards.
    """
    out = set()
    for root, _dirs, files in os.walk(SRC):
        for f in files:
            if f.endswith(".jsx") and not f.endswith((".test.jsx", ".dom.test.jsx")):
                out.add(os.path.join(root, f))
    return out


# --------------------------------------------------------------------------- the route table
def registered_routes() -> list[tuple[str, str, str]]:
    """(method, path, endpoint name) for every route on the app, included routers walked.

    FastAPI keeps an included router as one wrapper object rather than splicing its routes into
    `app.routes`, so a flat read of `app.routes` misses `/api/export/*`, `/api/fork/*` and every
    other mounted router -- which would make this test pass by not looking.
    """
    from harnesslab.backend.app import app

    def walk(routes, prefix=""):
        out = []
        for r in routes:
            inner = getattr(r, "original_router", None)
            if inner is not None:                       # an included router
                out += walk(inner.routes, prefix + (getattr(r.include_context, "prefix", "") or ""))
                continue
            path = getattr(r, "path", None)
            if path is None or getattr(r, "routes", None) is not None:   # a Mount
                continue
            ep = getattr(r, "endpoint", None)
            name = f"{getattr(ep, '__module__', '')}.{getattr(ep, '__qualname__', '')}"
            for m in sorted(getattr(r, "methods", None) or []):
                if m in ("HEAD", "OPTIONS"):
                    continue
                out.append((m, prefix + path, name))
        return out

    return walk(app.routes)


def _call_pattern(path: str) -> re.Pattern:
    """A regex that finds a mention of `path` in source text.

    Four things it has to tolerate. The shell reaches the API through `api()`, which prepends
    `/api`, so call sites are written without that prefix. Path parameters appear as `${dir}` in
    a template literal and `{d}` in an f-string. A longer path must not count as a call to its
    own prefix: `/api/results/x/runs/r7` is not a call to `/api/results/{name}/runs`. And a
    parameter can be CONCATENATED rather than interpolated -- field.html writes
    `"/api/jobs/" + encodeURIComponent(id) + "/cancel"` -- which reads as a call to any human and
    was invisible to this matcher, so a route the Field document calls looked dead.
    """
    body = re.escape(path[len("/api"):]) if path.startswith("/api/") else re.escape(path)
    hole = r"""(?:[^/'"`\s]+|["'`]\s*\+[^+]*\+\s*["'`])"""
    body = re.sub(r"\\\{[^}]*\\?\}", lambda _m: hole, body)   # a callable: `hole` is a pattern, not a template
    return re.compile(r"(?:/api)?" + body + r"(?![A-Za-z0-9_./{$-])")


#: `client.post("/api/x"` / `c.get(f"/api/x"` -- the verb sits immediately before the URL.
_VERB_BEFORE = re.compile(r"""\.(get|post|put|patch|delete)\(\s*[fr]?["']$""", re.I)
#: `api('/x', { method: 'POST', ... })` -- the verb sits just after it, in the options object.
_VERB_AFTER = re.compile(r"""method\s*:\s*['"](\w+)['"]""")


def _method_at(text: str, start: int, end: int) -> str:
    """The HTTP verb the call site around `text[start:end]` uses.

    A bare URL in a list, an `<a href>`, a `useFetch()` -- anything that writes the verb down
    nowhere -- is a read. That asymmetry is deliberate: it keeps the check strict where it
    matters, because a write route nothing writes to is not merely dead, it is attack surface.
    """
    m = _VERB_BEFORE.search(text[max(0, start - 24):start])
    if m:
        return m.group(1).upper()
    m = _VERB_AFTER.search(text[end:end + 240])
    return m.group(1).upper() if m else "GET"


def is_called(method: str, path: str, text: str) -> bool:
    """True when `text` calls `path` with `method` -- not merely mentions the path."""
    return any(_method_at(text, m.start(), m.end()) == method
               for m in _call_pattern(path).finditer(text))


def test_text() -> str:
    """Every test under tests_agentlab/, concatenated. This file itself is not evidence."""
    here = os.path.basename(__file__)
    chunks = []
    for root, _dirs, files in os.walk(TESTS):
        for f in sorted(files):
            if f.endswith(".py") and f != here:
                with open(os.path.join(root, f), encoding="utf-8") as fh:
                    chunks.append(fh.read())
    return "\n".join(chunks)


def caller_text() -> str:
    """Every source a route is allowed to be called from, concatenated."""
    chunks = []
    for f in sorted(reachable_files()):
        if f.endswith((".js", ".jsx")):
            with open(f, encoding="utf-8") as fh:
                chunks.append(fh.read())
    chunks.append(test_text())
    for p in (os.path.join(LAB, "harnesslab", "backend", "static_export.py"),
              os.path.join(LAB, "harnesslab", "__main__.py"),
              # A served document is a caller. field.html is handed to a browser by
              # GET /api/field/html and fetches this app's routes from there -- including the cancel
              # button on every running job. Leaving it out of this list is what let a route the
              # Field view calls read as dead: deleting it did not remove the button, it only made
              # the button POST into the SPA catch-all, take a 200 back, and report success for a
              # job that kept running.
              os.path.join(LAB, "harnesslab", "backend", "field.html")):
        with open(p, encoding="utf-8") as fh:
            chunks.append(fh.read())
    return "\n".join(chunks)


def handlers_a_test_calls(text: str) -> set[str]:
    """Handler names a test invokes as a plain function, through a module alias: `X.import_model(`.

    Qualified on purpose. A bare `cancel(` or `job(` would match half the suite by accident, and a
    guard that passes on an accident is not a guard.
    """
    return set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\.([A-Za-z_][A-Za-z0-9_]*)\s*\(", text))


class EveryCallHasARoute(unittest.TestCase):
    """The other direction, which the checks below do not cover.

    They ask "is this route called?". Deleting a route its caller still names passes that question
    silently -- the call then lands on the SPA catch-all, takes a 200 with index.html back, and the
    caller reports success. That is how the Field document's cancel button came to report a job
    cancelled while the job kept running.

    field.html is served by BOTH servers -- GET /api/field/html on the FastAPI app, and
    core/serve.py's stdlib server -- and they do not answer the same routes, so a call is satisfied
    when either one serves it. That split is a real thing about this repo, not an artefact of the
    test: `/api/launch` and `/api/mate/backends` are legacy-only, so those buttons are dead whenever
    the document is served by the FastAPI app.
    """

    @staticmethod
    def urls_fetched(doc: str) -> set[str]:
        """Every URL field.html fetches, with each interpolated or concatenated part as `{}`."""
        out = set()
        for call in re.finditer(r"fetch\(\s*(.+?)(?:,\s*\{|\))", doc, re.S):
            expr = call.group(1)
            if "/api/" not in expr:
                continue
            url, i = "", 0
            for lit in re.finditer(r"""["'`]([^"'`]*)["'`]""", expr):
                if lit.start() > i:
                    url += "{}"                       # an expression stood between two literals
                url += re.sub(r"\$\{[^}]*\}", "{}", lit.group(1))
                i = lit.end()
            url = url.split("?")[0].rstrip("/")
            if url.startswith("/api/"):
                out.add(url)
        return out

    @staticmethod
    def served_patterns() -> set[str]:
        fast = {p.rstrip("/") for _m, p, _ep in registered_routes() if p not in FRAMEWORK_PATHS}
        with open(os.path.join(LAB, "harnesslab", "core", "serve.py"), encoding="utf-8") as f:
            legacy = set(re.findall(r"""u\.path(?:\.startswith)?\s*(?:==|\()\s*["']([^"']+)["']""", f.read()))
        return {re.sub(r"\{[^}]*\}", "{}", p) for p in fast | {l.rstrip("/") for l in legacy}}

    def test_every_api_path_the_field_document_fetches_is_served_by_one_of_them(self):
        with open(os.path.join(LAB, "harnesslab", "backend", "field.html"), encoding="utf-8") as f:
            doc = f.read()
        served = self.served_patterns()
        missing = sorted(u for u in self.urls_fetched(doc)
                         if u not in served and not any(u.startswith(s + "/") for s in served))
        self.assertEqual([], missing,
                         "field.html fetches these and NEITHER server answers them -- the call takes a "
                         "200 from the SPA catch-all and reports success:\n  " + "\n  ".join(missing))


class NoOrphanComponents(unittest.TestCase):
    def test_every_jsx_is_reachable_from_the_entry(self):
        orphans = sorted(os.path.relpath(p, LAB) for p in all_jsx() - reachable_files())
        self.assertEqual([], orphans,
                         "these components are on disk but nothing the browser loads imports them:\n  "
                         + "\n  ".join(orphans))


class NoUncalledRoutes(unittest.TestCase):
    def test_every_route_has_a_caller(self):
        text = caller_text()
        direct = handlers_a_test_calls(test_text())
        routes = registered_routes()
        # A handler registered under two spellings (the importer answers on `` and on `/`) counts
        # as called when either spelling is called, so match on the endpoint, not on the path.
        called = {ep for m, p, ep in routes
                  if p not in FRAMEWORK_PATHS
                  and (is_called(m, p, text) or ep.rsplit(".", 1)[-1] in direct)}
        dead = sorted(f"{m} {p}" for m, p, ep in routes
                      if p not in FRAMEWORK_PATHS and ep not in called
                      and (m, p) not in KEPT_WITHOUT_CALLER)
        self.assertEqual([], dead,
                         "these routes are registered but nothing calls them -- not the shell, not a "
                         "test, not the CLI, not the static export:\n  " + "\n  ".join(dead))

    def test_the_exemption_list_has_not_gone_stale(self):
        """An exemption that has acquired a caller is no longer an exemption."""
        text = caller_text()
        direct = handlers_a_test_calls(test_text())
        by_path = {p: ep.rsplit(".", 1)[-1] for _m, p, ep in registered_routes()}
        live = sorted(f"{m} {p}" for m, p in KEPT_WITHOUT_CALLER
                      if is_called(m, p, text) or by_path.get(p) in direct)
        self.assertEqual([], live,
                         "KEPT_WITHOUT_CALLER names routes that now have callers; drop them from it:\n  "
                         + "\n  ".join(live))


if __name__ == "__main__":
    unittest.main()
