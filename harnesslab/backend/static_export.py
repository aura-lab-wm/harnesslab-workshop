"""One self-contained HTML file with the data baked in.

A running server is not a deliverable: a participant cannot hand one in, a reviewer cannot open
one six months later, and a supplementary link that needs `pip install` is a link most people
will not follow. This writes the built UI plus a snapshot of every read-only API response into a
single file that opens from disk with no server, no network and no dependencies.

    python -m harnesslab --export me.html
    python -m harnesslab --export me.html --export-results live
    python -m harnesslab --export me.html --export-results live --export-runs 0   # skip run details

The snapshot is taken by calling the running app through Starlette's TestClient rather than by
calling the metrics functions directly, so what lands in the file is byte-for-byte what the
server would have answered — a hand-rolled second path is exactly how a static export starts
disagreeing with the live one.

Live actions (launching runs, training, importing) are absent by construction: there is nothing
to POST to. The frontend detects the embedded payload, serves reads from it, refuses writes, and
matches on the *normalised query string* so a request carrying parameters the snapshot lacks
fails loudly instead of quietly resolving to the wrong numbers.
"""
from __future__ import annotations
import json, os, re, time
from urllib.parse import quote

from .paths import DIST, RUNS_ROOT
from .private_guard import OPT_IN_HEADER
from .results_scope import is_private, list_results_dirs

# How many per-run detail payloads to embed. Trajectories needs one per run it can open; all 576
# of a live directory would make the file unwieldy, so this is a bounded, stated sample.
DEFAULT_RUNS = 40


#: Keys stripped from a payload before it is embedded. An export is handed to a participant or a
#: reviewer, so nothing in it may identify the machine that produced it. /api/settings answers with
#: `key_hint` -- seven leading and four trailing characters of the operator's OpenRouter key -- and
#: with four absolute paths that embed the username. The page keeps working offline: what a reader
#: legitimately needs (whether a key is configured, which dirs and harnesses exist) survives.
#: `/import/status` reports the path that was imported, expanded -- so after one import of a
#: session store it carries /Users/<name>/.claude/projects, both at the top level and again inside
#: `result`. A dotted name reaches one level down; the whole payload is frozen into the export, so
#: the nested copy leaks exactly as loudly as the outer one.
REDACT = {"/settings": ("key_hint", "lab_root", "results_root", "harness_dir", "task_dir", "base_url"),
          "/import/status": ("path", "result.path")}


def redact(path: str, payload):
    """`payload` without the keys REDACT names for `path`. Anything unlisted passes through.

    A name may be dotted (`result.path`) to drop a key from a nested object, which is left alone
    when it is absent or is not an object -- an idle import has `result: None`.
    """
    drop = REDACT.get(path)
    if not drop or not isinstance(payload, dict):
        return payload
    nested: dict[str, set] = {}
    for name in drop:
        if "." in name:
            outer, inner = name.split(".", 1)
            nested.setdefault(outer, set()).add(inner)
    out = {k: v for k, v in payload.items() if k not in drop}
    for outer, inners in nested.items():
        if isinstance(out.get(outer), dict):
            out[outer] = {k: v for k, v in out[outer].items() if k not in inners}
    return out


#: URLs that answer the same way regardless of which results directory is being exported --
#: fetched once by build(), outside the per-dir generation-pinning window (see "PIN ONE
#: GENERATION" there), since there is no study revision for them to ever disagree about.
_GLOBAL_URLS = ["/api/overview", "/api/tasks", "/api/harnesses", "/api/models",
                "/api/sentinel", "/api/sentinel/plugins", "/api/judge",
                "/api/harness/list", "/api/harness/knobs",
                "/api/import/sources", "/api/import/status", "/api/real/status",
                "/api/export/formats", "/api/settings"]


def _dir_urls(client, d: str, n_runs: int) -> list[str]:
    """Every read-only URL ONE results dir's pages actually request.

    Split out of _urls() so build() can fetch one dir's whole set inside a single
    generation-pinned window: every URL here reads `d`'s index.jsonl (directly, or via a
    study-aggregate endpoint that reads it), so a comparisons response and an oracle response
    baked for the same `d` must come from one read of it, never two.
    """
    ov = client.get(f"/api/results/{d}/runs")
    rows = ov.json() if ov.status_code == 200 else []
    harnesses = sorted({r["harness_id"] for r in rows}) or ["baseline"]
    q = lambda v: quote(str(v), safe="")   # noqa: E731 - local shorthand
    out = [f"/api/results/{d}/runs", f"/api/results/{d}/metrics",
           f"/api/results/{d}/experiment", f"/api/real/{d}/analysis",
           f"/api/harness/versions?dir={d}", f"/api/harness/list?dir={d}",
           f"/api/fork/{d}/pairs?limit=80",
           f"/api/sentinel/leaderboard?dir={d}&k=10%2C15%2C20&threshold=0.6",
           f"/api/field/html?results={d}",
           # RepeatsChart (mounted in the trajectories tab, StepAttribute.jsx) fetches this
           # with no `harness` param first; the per-harness variants below cover the filtered
           # view the tab's own harness picker switches to.
           f"/api/repeats/{d}",
           # Failure modes per run (outcomes_api.py): the Rig workbench's failure chips and
           # its "how do runs fail?" answer read this; one bake per dir.
           f"/api/outcomes/{d}",
           # Judge and report's agreement summary (docs/superpowers/specs/2026-09-21-study-
           # aggregates-design.md): no parameters to vary, one bake per dir.
           f"/api/results/{d}/oracle",
           # The attribution matrix: every model's per-harness comparison in one response, at
           # its bare (server-picked) baseline HARNESS. That bare form is what the matrix
           # requests today; one bake per real harness is added below so a baseline picker
           # would find its data offline. The baseline is a HARNESS id -- the first version of
           # this baked one variant per MODEL, which matched an endpoint that wrongly compared
           # models against each other.
           f"/api/results/{d}/comparisons"]
    for h in harnesses:
        out.append(f"/api/results/{d}/comparisons?baseline={q(h)}")
    for h in harnesses:
        out += [f"/api/results/{d}/runs?harness={q(h)}",
                f"/api/results/{d}/metrics?baseline={q(h)}",
                f"/api/results/{d}/integrity?harness={q(h)}",
                f"/api/results/{d}/report?harness={q(h)}",
                f"/api/results/{d}/patterns?harness={q(h)}",
                f"/api/sentinel/replay_all/{d}?harness={q(h)}",
                # the Sentinel page's leaderboard, at the defaults its controls start on
                f"/api/sentinel/leaderboard?dir={d}&k=10%2C15%2C20&threshold=0.6&harness={q(h)}",
                f"/api/repeats/{d}?harness={q(h)}"]
    for oracle in ("hidden_pass", "strong_pass", "visible_pass"):
        out.append(f"/api/harness/ranking?dir={d}&outcome={oracle}")
        # the three decks read the assistant's deterministic digest per (dir, harness, oracle)
        for h in harnesses:
            out.append(f"/api/assist/context?results={d}&harness={q(h)}&oracle={oracle}")
        for a, b in (("model", "harness_id"), ("harness_id", "model"),
                     ("model", "task_id"), ("harness_id", "task_id")):
            out.append(f"/api/results/{d}/experiment?a={a}&b={b}&outcome={oracle}")
    for r in rows[:n_runs]:
        # Same sample as the two per-run URLs above (rows[:n_runs], the bounded set this
        # module already accepts the size of): the per-call detail RepeatsChart fetches only
        # for the opened/highlighted run. A run outside this sample still renders its step
        # line from the /api/repeats/{d} series above; only its hover tooltip's per-call
        # command text needs the live app (RepeatsChart.jsx degrades that path already --
        # `detail` stays null and the tooltip just omits the command line).
        out += [f"/api/results/{d}/runs/{r['run_id']}", f"/api/fork/{d}/state/{r['run_id']}",
                f"/api/repeats/{d}/run/{r['run_id']}"]
    return out


def _urls(client, dirs: list[str], n_runs: int) -> list[str]:
    """Every read-only URL the pages actually request, expanded over the real dirs/harnesses.

    A flat, deduplicated concatenation of _GLOBAL_URLS and _dir_urls() per dir -- this is the
    shape test_static_export_coverage.py and the export-completeness test both check against;
    build() itself no longer calls this directly (it needs the per-dir split to pin one
    generation per study), but every URL it fetches still comes from exactly these two builders.
    """
    out = list(_GLOBAL_URLS)
    for d in dirs:
        out += _dir_urls(client, d, n_runs)
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u); uniq.append(u)
    return uniq


def default_dirs() -> list[str]:
    """Results directories a default export includes: never the private ones (see results_scope)."""
    return list_results_dirs(RUNS_ROOT)


#: How many times build() re-reads a dir's WHOLE URL set from scratch when its index.jsonl moved
#: under it mid-bake, before giving up on that one dir (see "PIN ONE GENERATION" in build()).
_GENERATION_ATTEMPTS = 3


def _generation_fingerprint(d: str) -> tuple:
    """The identity of results dir `d`'s index.jsonl right now: a thin, deferred-import wrapper
    around app.py's `_study_fingerprint` -- the SAME identity `_StudyCache` and
    `_study_rows_stable` already key their own freshness on, so "this dir's cached responses are
    fresh" and "this dir did not move during the bake" are the same check. Kept as its own
    module-level name (rather than inlined at each call site in build()) so it -- and only the
    two checks build() makes around a dir's whole URL batch -- can be patched in a test without
    touching every request-scoped fingerprint check the live routes make internally."""
    from .app import _study_fingerprint
    return _study_fingerprint(d)


def _fetch(client, url: str, sink: dict, fail_sink: list) -> None:
    """One GET, filed into `sink` (keyed the way the frontend's request-cache key is: the URL
    with the leading /api stripped) on 200, or into `fail_sink` otherwise. Shared by build()'s
    global-URL pass and its per-dir generation-pinned pass so both fail the same way."""
    try:
        r = client.get(url)
        if r.status_code == 200:
            key = url[len("/api"):]
            sink[key] = redact(key, r.json())
        else:
            fail_sink.append((url, r.status_code))
    except Exception as e:                                # one bad URL must not sink the export
        fail_sink.append((url, f"{type(e).__name__}: {e}"))


def build(out_path: str, results: list[str] | None = None, n_runs: int = DEFAULT_RUNS) -> dict:
    index = os.path.join(DIST, "index.html")
    if not os.path.exists(index):
        raise SystemExit("the UI is not built. Run: cd harnesslab/frontend && npm install && npm run build")

    dirs = results or default_dirs()
    if not dirs:
        raise SystemExit(f"no results directories under {RUNS_ROOT}")

    from starlette.testclient import TestClient
    from .app import app

    # confirm_private_export() already gated a private dir on an explicit yes (see __main__.py);
    # this is the same opt-in the HTTP guard needs to actually serve it through the TestClient.
    private = any(is_private(RUNS_ROOT, d) for d in dirs)
    data, failed = {}, []
    with TestClient(app, headers={OPT_IN_HEADER: "1"} if private else None) as client:
        for url in _GLOBAL_URLS:                          # no study revision to disagree about
            _fetch(client, url, data, failed)

        # PIN ONE GENERATION for the whole bake (docs/superpowers/specs/2026-09-21-study-
        # aggregates-design.md #4): comparisons, oracle, experiment/fit and every other
        # study-aggregate response embedded for one dir must all come from the SAME read of its
        # index.jsonl, never from two different writer generations landing in the same exported
        # file. _study_fingerprint (the identity _StudyCache and _study_rows_stable already key
        # on in app.py: real path, st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns) is stat'd
        # here BEFORE requesting `d`'s whole URL set and again AFTER; only a match commits the
        # batch. A mismatch means index.jsonl was touched mid-bake -- rather than mixing that
        # batch's early responses (read under the old generation) with anything baked afterwards
        # (a new generation), the WHOLE batch for `d` is discarded and re-fetched from scratch,
        # up to _GENERATION_ATTEMPTS times. A dir that still will not hold still is dropped from
        # the export entirely (recorded once in `failed`, none of its keys baked) rather than
        # shipped as a silent mix of generations. This is the same best-effort guarantee
        # app.py's own fingerprint documents -- it narrows the race, it does not close it -- but
        # it is a real, checkable improvement over baking each URL independently with no relation
        # between them at all.
        for d in dirs:
            pinned, last_mismatch = False, None
            for _attempt in range(_GENERATION_ATTEMPTS):
                try:
                    fp_before = _generation_fingerprint(d)
                except OSError as e:
                    failed.append((f"(generation) {d}", f"{type(e).__name__}: {e}"))
                    break
                snap, snap_failed = {}, []
                for url in _dir_urls(client, d, n_runs):
                    _fetch(client, url, snap, snap_failed)
                try:
                    fp_after = _generation_fingerprint(d)
                except OSError as e:
                    failed.append((f"(generation) {d}", f"{type(e).__name__}: {e}"))
                    break
                if fp_before == fp_after:
                    data.update(snap)
                    failed += snap_failed
                    pinned = True
                    break
                last_mismatch = (fp_before, fp_after)
            if not pinned and last_mismatch is not None:
                failed.append((f"(generation) {d}",
                               f"index.jsonl changed during export across {_GENERATION_ATTEMPTS} "
                               f"attempts; {d}'s study-aggregate endpoints were skipped rather "
                               f"than baked from a mixed generation"))

    data["__meta__"] = {"generated_at": time.strftime("%Y-%m-%d %H:%M"), "results": dirs,
                        "static": True, "runs_embedded": n_runs}

    html = open(index, encoding="utf-8").read()

    def inline(m):
        tag, url = m.group(0), m.group(2)
        p = os.path.join(DIST, url.lstrip("/"))
        if not os.path.exists(p):
            return tag
        body = open(p, encoding="utf-8").read()
        if url.endswith(".css"):
            return f"<style>\n{body}\n</style>"
        if url.endswith(".js"):
            return f'<script type="module">\n{body}\n</script>'
        return tag

    # drop remote font links: a file meant to open offline must not hang on a network fetch.
    # The CSS carries a system-font fallback stack, so this costs the webfont and nothing else.
    html = re.sub(r'<link[^>]+fonts\.(googleapis|gstatic)\.com[^>]*>', "", html)
    html = re.sub(r'<link[^>]*rel="stylesheet"[^>]*(href)="([^"]+)"[^>]*>', inline, html)
    html = re.sub(r'<script[^>]*(src)="([^"]+)"[^>]*></script>', inline, html)

    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    banner = f"<script>window.__HARNESSLAB_DATA__={blob};</script>"
    html = html.replace("</head>", banner + "\n</head>", 1) if "</head>" in html else banner + html

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return {"path": os.path.abspath(out_path), "bytes": len(html.encode()),
            "results": dirs, "endpoints": len(data) - 1, "failed": failed,
            "runs_embedded": n_runs}
