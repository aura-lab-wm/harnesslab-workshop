"""Whole-site smoke test for the Rig: every view, every dataset, in a real browser.

    python3 scripts/smoke_rig.py                        # starts its own server on a free port
    python3 scripts/smoke_rig.py --base http://127.0.0.1:8765
    python3 scripts/smoke_rig.py --quick                # one dataset, one theme, one width
    python3 scripts/smoke_rig.py --export               # also build and check a static export

A page FAILS when any of these happen:
  * an uncaught page error (a JS exception reached window.onerror)
  * a React error logged to the console
  * the per-tab error boundary ("This view failed while drawing")
  * a not-found document for a spec that should exist
  * a blank document, or one still loading after the timeout
  * the page scrolls sideways (document wider than the window)

It also checks the degraded modes: the bundle failing to load (the page must say why, not go
blank); the server stopping while the Rig is open (the shell must say so and keep drawing) and
then coming back (the open page must heal without a reload); and a static export opened from
disk (no network at all).

Needs Playwright for Python (`pip install playwright`). It uses an installed Chromium; set
HARNESSLAB_CHROMIUM to a browser binary if Playwright cannot find one.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUESTIONS = ["variable", "grader", "repeats", "outcomes", "leak", "run", "sentinel", "report", "next"]
AN_VIEWS = ["family", "outcomes", "delta", "judge", "fit", "report", "setup"]
GLOBAL = ["home", "sources", "capture", "sentinel", "canvas", "settings", "guide", "package"]
DOCKS = ["buddy", "log", "capture", "case"]
WIDTHS = [(1512, 860), (1100, 780), (390, 844)]

PROBE = """() => {
  const docs = [...document.querySelectorAll('.rg-doc')].filter(d => d.offsetParent !== null)
  const doc = docs[0]
  const text = doc ? doc.innerText.trim() : ''
  return {
    rig: !!document.querySelector('.rig'),
    hash: location.hash,
    crashed: !!document.querySelector('[data-el="view-crash"], [data-el="app-crash"]'),
    notFound: !!(doc && doc.querySelector('[data-el="not-found"]')),
    loading: !!(doc && doc.querySelector('[data-el="loading-state"]')),
    textLen: text.length,
    overflow: document.documentElement.scrollWidth - window.innerWidth,
    title: document.title,
  }
}"""


def chromium_path() -> str | None:
    env = os.environ.get("HARNESSLAB_CHROMIUM")
    if env:
        return env
    pats = [
        "~/Library/Caches/ms-playwright/chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        "~/Library/Caches/ms-playwright/chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
        "~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome",
        "/opt/pw-browsers/chromium*/chrome-linux*/chrome",
    ]
    for pat in pats:
        hits = sorted(glob.glob(os.path.expanduser(pat)))
        if hits:
            return hits[-1]
    return None


def free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    return port


def get_json(url: str):
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read())


def start_server(port: int, lab: str | None) -> subprocess.Popen:
    env = dict(os.environ, HARNESSLAB_NO_BROWSER="1")
    if lab:
        env["HARNESSLAB_LAB"] = lab
    proc = subprocess.Popen([sys.executable, "-m", "harnesslab", "--port", str(port), "--no-browser"],
                            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for _ in range(80):
        try:
            get_json(f"http://127.0.0.1:{port}/api/overview")
            return proc
        except Exception:
            if proc.poll() is not None:
                raise SystemExit("server exited: " + proc.stderr.read().decode()[-800:])
            time.sleep(0.25)
    proc.kill()
    raise SystemExit("server did not come up")


def specs_for(base: str, dataset: dict) -> list[str]:
    d = dataset["name"]
    runs = get_json(f"{base}/api/results/{urllib.request.quote(d)}/runs")
    out = [f"ds:{d}", f"q:{d}", f"tasks:{d}", f"field:{d}"]
    out += [f"q:{d}:{q}" for q in QUESTIONS]
    out += [f"an:{d}:{v}" for v in AN_VIEWS]
    if runs:
        r0 = runs[0]
        model = r0.get("model", ""); model = model.split("/", 1)[-1] if "/" in model else model
        out.append(f"task:{d}:{model}:{r0.get('harness_id', '')}:{r0.get('task_id', '')}")
        out.append(f"run:{r0['run_id']}")
        out.append(f"span:{r0['run_id']}:1")
        out.append(f"fork:{r0['run_id']}")
        fail = next((r for r in runs if r.get("hidden_pass") is False), None)
        ok = next((r for r in runs if r.get("hidden_pass") is True), None)
        if fail and ok:
            out.append(f"cmp:{fail['run_id']}:{ok['run_id']}")
    return out


def enc(spec: str) -> str:
    # model ids may carry ':' (glm-5.2:cloud) -- the Rig escapes it; our specs here never do.
    return spec.replace(" ", "%20")


class Smoke:
    def __init__(self, page, base: str, timeout_ms: int):
        self.page, self.base, self.timeout = page, base, timeout_ms
        self.failures: list[str] = []
        self.checked = 0
        self.errors: list[str] = []
        page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: self.errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" and not _benign(m.text) else None)

    def visit(self, hash_: str, label: str, expect_found: bool = True, min_text: int = 20):
        self.errors.clear()
        self.page.goto(self.base + "/" + hash_)
        return self.check(label, expect_found, min_text)

    def check(self, label: str, expect_found: bool = True, min_text: int = 20):
        deadline = time.time() + self.timeout / 1000
        info = None
        while time.time() < deadline:
            self.page.wait_for_timeout(250)
            info = self.page.evaluate(PROBE)
            if info["rig"] and not info["loading"] and info["textLen"] >= min_text:
                break
        self.page.wait_for_timeout(200)
        info = self.page.evaluate(PROBE)
        self.checked += 1
        bad = []
        if not info["rig"]:
            bad.append("no Rig on the page (blank)")
        if info["crashed"]:
            bad.append("a view crashed (error boundary shown)")
        if expect_found and info["notFound"]:
            bad.append("not-found document")
        if info["loading"]:
            bad.append("still loading after timeout")
        elif info["textLen"] < min_text:
            bad.append(f"almost empty document ({info['textLen']} chars)")
        if info["overflow"] > 1:
            bad.append(f"page scrolls sideways by {info['overflow']}px")
        bad += self.errors[:3]
        if bad:
            self.failures.append(f"{label}  [{info['hash']}]\n      " + "\n      ".join(bad))
        return info


def _benign(text: str) -> bool:
    # A 4xx/5xx the UI asked for and then explained is not a crash; the views render those states.
    return "Failed to load resource" in text


def run(args) -> int:
    from playwright.sync_api import sync_playwright

    proc = None
    base = args.base
    if not base:
        port = free_port()
        proc = start_server(port, args.lab)
        base = f"http://127.0.0.1:{port}"
    try:
        datasets = get_json(base + "/api/overview")["results"]
        if args.quick:
            datasets = sorted(datasets, key=lambda d: -d["runs"])[:1]
        widths = [] if args.degraded else WIDTHS[:1] if args.quick else WIDTHS
        themes = ["dark"] if args.quick else ["dark", "light"]
        with sync_playwright() as p:
            exe = chromium_path()
            browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
            total_fail: list[str] = []
            checked = 0
            for (w, h) in widths:
                for theme in themes:
                    ctx = browser.new_context(viewport={"width": w, "height": h})
                    page = ctx.new_page()
                    s = Smoke(page, base, args.timeout)
                    q = f"?theme={theme}"
                    for spec in GLOBAL:
                        s.visit(f"#/rig/{spec}{q}", f"{w}px {theme} {spec}")
                    if w >= 1400:
                        # Settings: the two key panels sit side by side and share their rows.
                        s.visit(f"#/rig/settings{q}", f"{w}px {theme} settings alignment")
                        g = page.evaluate("""() => { const t = (s) => { const e = document.querySelector(s); return e ? Math.round(e.getBoundingClientRect().top) : null }
                          return [t('[data-el=buddy-settings] input[type=password]'), t('[data-el=lab-key] input[type=password]'), t('[data-el=buddy-settings] .rg-ws-acts'), t('[data-el=lab-key] .rg-ws-acts')] }""")
                        if None not in g and (g[0] != g[1] or g[2] != g[3]):
                            s.failures.append(f"{w}px {theme} settings: key fields / buttons not aligned {g}")
                    if w > 760:
                        # Canvas: the tree spreads over the width it is given, with no sideways scroll at 100%.
                        s.visit(f"#/rig/canvas{q}", f"{w}px {theme} canvas spread")
                        g = page.evaluate("""() => { const w = document.querySelector('.rg-ws-cvwrap'); const n = [...document.querySelectorAll('.rg-ws-node')]
                          if (!w || !n.length) return null; const r = w.getBoundingClientRect(); const right = Math.max(...n.map(e => e.getBoundingClientRect().right))
                          return { fill: (right - r.left) / r.width, over: w.scrollWidth - w.clientWidth } }""")
                        if g and (g["fill"] < 0.6 or g["over"] > 1):
                            s.failures.append(f"{w}px {theme} canvas: tree uses {g['fill']:.0%} of the width, overflow {g['over']}px")
                    for d in datasets:
                        for spec in specs_for(base, d):
                            s.visit(f"#/rig/{enc(spec)}{q}", f"{w}px {theme} {spec}")
                    if w > 760 and datasets:
                        d0 = datasets[0]["name"]
                        s.visit(f"#/rig/ds:{d0}|field:{d0}{q}", f"{w}px {theme} split")
                        s.visit(f"#/rig/ds:{d0}{q}&max=1", f"{w}px {theme} maximized")
                        for dock in DOCKS:
                            s.visit(f"#/rig/ds:{d0}{q}&dock={dock}", f"{w}px {theme} dock {dock}")
                    # legacy console links always land somewhere real
                    for legacy in ["#/", "#/s/" + (datasets[0]["name"] if datasets else "x") + "/judge", "#/nowhere/at/all"]:
                        s.visit(legacy, f"{w}px {theme} legacy {legacy}")
                    # garbage links never blank the page
                    for junk in ["#/rig/%%%", "#/rig/nope:1:2", "#/rig/ds:no_such_dataset", "#/rig/run:zzzzzz"]:
                        s.visit(junk, f"{w}px {theme} junk {junk}", expect_found=False)
                    total_fail += s.failures
                    checked += s.checked
                    ctx.close()
                    print(f"  {w}px {theme}: {s.checked} pages, {len(s.failures)} failing", flush=True)

            # degraded: the bundle itself does not load (a stale index.html, a deleted asset)
            ctx = browser.new_context(viewport={"width": 1512, "height": 860})
            page = ctx.new_page()
            page.route("**/assets/*.js", lambda r: r.fulfill(status=404, body="gone"))
            page.goto(base + "/")
            page.wait_for_timeout(1500)
            boot = page.evaluate("(document.getElementById('hl-boot')||{}).innerText||''")
            checked += 1
            if "did not load" not in boot:
                total_fail.append(f"missing bundle: the page does not explain itself (shows {boot[:80]!r})")
            ctx.close()
            print("  missing bundle: 1 check", flush=True)

            # degraded: the backend goes away while the Rig is open, then comes back
            if proc is not None and not args.quick:
                port = int(base.rsplit(":", 1)[1])
                ctx = browser.new_context(viewport={"width": 1512, "height": 860})
                page = ctx.new_page()
                s = Smoke(page, base, args.timeout)
                d = [x["name"] for x in datasets]
                status = lambda: page.evaluate("(document.querySelector('[data-el=runtime-status]')||{}).textContent||''")
                s.visit(f"#/rig/ds:{d[0]}", "outage: before")
                proc.terminate(); proc.wait(10); proc = None
                s.errors.clear()
                # keep working: open views that were never loaded, so their requests hit nobody
                for spec in ["settings", f"field:{d[-1]}", f"an:{d[-1]}:fit"]:
                    page.evaluate(f"location.hash = '#/rig/{spec}'")
                    s.check(f"outage: open {spec}", expect_found=False, min_text=0)
                deadline = time.time() + 12
                while time.time() < deadline and "unreachable" not in status():
                    page.wait_for_timeout(500)
                if "unreachable" not in status():
                    total_fail.append(f"outage: status bar still says {status()!r} with the server stopped")
                # the server comes back on the same port: the open workbench heals without a reload
                proc = start_server(port, args.lab)
                deadline = time.time() + 15
                while time.time() < deadline and "connected" not in status():
                    page.wait_for_timeout(500)
                if "connected" not in status():
                    total_fail.append(f"recovery: status bar says {status()!r} after the server came back")
                s.errors.clear()
                info = s.check("recovery: the page that failed during the outage", min_text=20)
                # during the outage an error state is correct; a crash or a blank page never is
                total_fail += [f for f in s.failures if not f.startswith("outage: open") or "error boundary" in f or "no Rig" in f]
                checked += s.checked
                ctx.close()
                print(f"  outage and recovery: {s.checked} checks", flush=True)

            if args.export:
                out = os.path.join(tempfile.mkdtemp(), "lab.html")
                env = dict(os.environ)
                if args.lab:
                    env["HARNESSLAB_LAB"] = args.lab
                small = sorted(datasets, key=lambda d: d["runs"])[0]["name"]
                subprocess.run([sys.executable, "-m", "harnesslab", "--export", out, "--export-results", small,
                                "--export-runs", "5", "--yes"], cwd=ROOT, env=env, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                ctx = browser.new_context(viewport={"width": 1512, "height": 860})
                page = ctx.new_page()
                net = []
                page.on("request", lambda r: net.append(r.url) if not r.url.startswith(("file:", "data:", "blob:")) else None)
                s = Smoke(page, "file://" + out, args.timeout)
                s.base = "file://" + out
                for h in ["", f"#/rig/ds:{small}", f"#/rig/an:{small}:outcomes", f"#/rig/field:{small}", f"#/s/{small}/measure"]:
                    s.errors.clear(); page.goto(s.base + h); s.check(f"export {h or '(root)'}")
                if net:
                    total_fail.append(f"export made network requests: {net[:3]}")
                total_fail += s.failures
                checked += s.checked
                ctx.close()
                print(f"  static export: {s.checked} pages", flush=True)
            browser.close()
    finally:
        if proc is not None:
            proc.terminate()

    print(f"\n{checked} checks, {len(total_fail)} failing")
    for f in total_fail[: args.show]:
        print("  FAIL " + f)
    if len(total_fail) > args.show:
        print(f"  ... and {len(total_fail) - args.show} more")
    return 1 if total_fail else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", help="use a running server instead of starting one")
    ap.add_argument("--lab", help="lab root for the server this script starts (HARNESSLAB_LAB)")
    ap.add_argument("--quick", action="store_true", help="largest dataset, dark theme, laptop width only")
    ap.add_argument("--export", action="store_true", help="also build and check a static export")
    ap.add_argument("--degraded", action="store_true", help="only the degraded modes (missing bundle, outage, export)")
    ap.add_argument("--timeout", type=int, default=8000, help="ms to wait for a page to settle")
    ap.add_argument("--show", type=int, default=40, help="failures to print")
    return run(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())
