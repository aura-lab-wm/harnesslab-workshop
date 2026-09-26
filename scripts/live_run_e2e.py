#!/usr/bin/env python3
"""Live end-to-end check for the run page, `harnesslab watch` and Stop -- headless, on a throwaway
server, never the one on :8765.

    PORT=$(~/.claude/bin/freeport 9000 9099)
    /opt/homebrew/bin/python3.14 scripts/live_run_e2e.py --port "$PORT" --out /path/to/scratch

Starts its own `python -m harnesslab` on 127.0.0.1:<port> against a scratch lab root (harnesses/
and tasks/ copied from this checkout, an empty data/runs/), with HARNESSLAB_MOCK_PACE_MS so a mock
run takes a few seconds, and drives it with headless Chromium (Playwright). Every check appends a
verdict entry; exit 0 only when all pass. Screenshots and the verdict land in --out.

Proves: (1) a WEB-APP run's page, opened mid-run, shows steps while the badge says running, then
reaches finished with the outcome block; Stop on the job's next run lets that run finish and keeps
the queued one from starting; (2) a CLI run's page shows steps mid-run with the terminal note (no
Stop) and reaches finished, while `harnesslab watch <dir>/<run_id>` printed the same steps and
exited 0; (3) a run id that does not exist renders the not-found page. Stdlib + playwright only.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
PACE_MS = "800"


def get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def post(url, body, timeout=10):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def wait_for(pred, timeout, every=0.2, what="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(every)
    raise RuntimeError(f"timed out after {timeout}s waiting for {what}")


class Check:
    def __init__(self, verdict, name):
        self.verdict, self.name, self.t0 = verdict, name, time.time()

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        self.verdict.append({"name": self.name, "ok": et is None, "detail": "" if et is None else f"{et.__name__}: {ev}",
                             "seconds": round(time.time() - self.t0, 2)})
        return True                                   # a failed check never stops the teardown


def open_local(page, base):
    """The app starts in school mode on every visit; the run page streams only in the local view."""
    page.goto(base + "/#/", wait_until="networkidle")
    page.select_option("select[aria-label='Workspace view']", "local")
    page.wait_for_selector("text=Local workspace", timeout=10000)


def goto_run(page, base, dirname, run_id):
    page.evaluate(f"location.hash = '#/run/{dirname}/{run_id}'")


JS_RUNNING_WITH_STEPS = ("document.querySelectorAll('.run-row').length >= 1 && "
                         "(document.querySelector('.run-badge') || {}).textContent.includes('running')")
JS_FINISHED = ("(document.querySelector('.run-badge') || {}).textContent.includes('finished') && "
               "!!document.querySelector('.run-finished') && "
               "[...document.querySelectorAll('h2')].some(h => h.textContent === 'Outcome')")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, required=True, help="a free 127.0.0.1 port from ~/.claude/bin/freeport")
    ap.add_argument("--out", type=Path, required=True, help="scratch directory for the lab root, screenshots and the verdict")
    args = ap.parse_args()
    from playwright.sync_api import sync_playwright   # imported here so --help works without it

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    lab = out / "e2e-lab"
    if lab.exists():
        shutil.rmtree(lab)
    (lab / "data" / "runs").mkdir(parents=True)
    shutil.copytree(ROOT / "harnesses", lab / "harnesses")
    shutil.copytree(ROOT / "tasks", lab / "tasks")
    base = f"http://127.0.0.1:{args.port}"
    env = {**os.environ, "HARNESSLAB_MOCK_PACE_MS": PACE_MS, "HARNESSLAB_NO_BROWSER": "1", "HARNESSLAB_LAB": str(lab)}
    verdict = []
    server = None
    errors = []
    try:
        with Check(verdict, f"start the throwaway server on 127.0.0.1:{args.port} (--no-browser, scratch lab)"):
            server = subprocess.Popen([PY, "-m", "harnesslab", "--lab", str(lab), "--host", "127.0.0.1", "--port", str(args.port), "--no-browser"],
                                      cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            wait_for(lambda: server.poll() is None and _up(base), 30, what="the server")
        if server is None or server.poll() is not None:
            raise SystemExit(json.dumps({"ok": False, "checks": verdict}, indent=2))

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.on("pageerror", lambda e: errors.append(str(e)))
            open_local(page, base)

            # ---- 1. a run started from the web app, opened mid-run ------------------------------
            job = None
            with Check(verdict, "web app: POST /api/jobs (mock, t01_slugify, 3 repeats, parallel 1)"):
                job = post(f"{base}/api/jobs", {"out": "e2e_web", "models": ["mock"], "harnesses": ["baseline"],
                                                "tasks": ["t01_slugify"], "repeats": 3, "parallel": 1})
            first = None
            with Check(verdict, "web app: the first run's page shows steps while running, then finishes with the outcome block"):
                first = wait_for(lambda: next((r for j in get(f"{base}/api/jobs") if j["id"] == job["id"] for r in j["running"]), None), 20, what="a running run")
                goto_run(page, base, "e2e_web", first)
                page.wait_for_function(JS_RUNNING_WITH_STEPS, timeout=20000)
                page.screenshot(path=str(out / "web-running.png"))
                page.wait_for_function(JS_FINISHED, timeout=60000)
                page.screenshot(path=str(out / "web-finished.png"))
                page.wait_for_selector("text=raw messages", timeout=10000)
            with Check(verdict, "web app: Stop on the second run lets it finish and keeps the third from starting"):
                second = wait_for(lambda: next((r for j in get(f"{base}/api/jobs") if j["id"] == job["id"] for r in j["running"] if r != first), None), 30, what="the second run")
                goto_run(page, base, "e2e_web", second)
                page.wait_for_function(JS_RUNNING_WITH_STEPS, timeout=20000)
                page.click("button:has-text('Stop')")
                page.wait_for_selector("text=queued runs will not start", timeout=10000)
                page.wait_for_function(JS_FINISHED, timeout=60000)
                page.screenshot(path=str(out / "web-stopped.png"))
                j = wait_for(lambda: next((j for j in get(f"{base}/api/jobs") if j["id"] == job["id"] and j["status"] not in ("queued", "running")), None), 60, what="the job to end")
                idx = (lab / "data" / "runs" / "e2e_web" / "index.jsonl").read_text().splitlines()
                if not (j["status"] == "cancelled" and j["done"] == 2 and j["total"] == 3 and len(idx) == 2):
                    raise RuntimeError(f"status={j['status']} done={j['done']} total={j['total']} index_lines={len(idx)}")

            # ---- 2. a run started from the CLI, watched in the terminal and on its page ----------
            cli = None
            with Check(verdict, "cli: python -m harnesslab run --provider mock writes a paced run into the scratch lab"):
                cli = subprocess.Popen([PY, "-m", "harnesslab", "run", "--provider", "mock", "--tasks", "t01_slugify", "--repeats", "1",
                                        "--out", str(lab / "data" / "runs" / "e2e_cli")], cwd=ROOT, env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            watch = None
            cli_run = None
            with Check(verdict, "cli: the run page shows steps while running, with the terminal note and no Stop, then finishes"):
                cli_run = wait_for(lambda: next((r["run_id"] for r in get(f"{base}/api/live") if r["dir"] == "e2e_cli" and r["state"] == "running"), None), 20, what="the CLI run in /api/live")
                watch = subprocess.Popen([PY, "-m", "harnesslab", "watch", "--lab", str(lab), f"e2e_cli/{cli_run}"], cwd=ROOT, env=env,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                goto_run(page, base, "e2e_cli", cli_run)
                page.wait_for_function(JS_RUNNING_WITH_STEPS, timeout=20000)
                page.wait_for_selector("text=started from the terminal", timeout=5000)
                if page.query_selector("button:has-text('Stop')"):
                    raise RuntimeError("a CLI run must not offer Stop")
                page.screenshot(path=str(out / "cli-running.png"))
                page.wait_for_function(JS_FINISHED, timeout=60000)
                page.screenshot(path=str(out / "cli-finished.png"))
            with Check(verdict, "cli: `harnesslab watch e2e_cli/<run_id>` printed the steps live and exited 0 at the end"):
                cli.wait(timeout=90)
                wout, _ = watch.communicate(timeout=30)
                lines = [l for l in wout.splitlines() if " │ " in l]
                if watch.returncode != 0 or len(lines) < 4 or "end:" not in lines[-1] or "run " not in lines[0]:
                    raise RuntimeError(f"rc={watch.returncode} lines={len(lines)}\n{wout[-1500:]}")
                (out / "watch.txt").write_text(wout)
                if cli.returncode != 0:
                    raise RuntimeError(f"cli run exited {cli.returncode}: {cli.stdout.read()[-1500:]}")

            # ---- 3. a run that does not exist ---------------------------------------------------
            with Check(verdict, "a missing run id renders the not-found page with the run list beside it"):
                goto_run(page, base, "e2e_cli", "does-not-exist")
                page.wait_for_selector("text=Run not found", timeout=10000)
                page.wait_for_selector("nav[aria-label='runs']", timeout=5000)
                page.screenshot(path=str(out / "not-found.png"))

            with Check(verdict, "no uncaught page errors in the browser during the whole session"):
                if errors:
                    raise RuntimeError("; ".join(errors[:5]))
            browser.close()
    finally:
        if server is not None and server.poll() is None:
            server.terminate()                        # exact pid: the process this script started
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
        (out / "verdict.json").write_text(json.dumps({"ok": all(c["ok"] for c in verdict), "port": args.port, "checks": verdict}, indent=2))
    ok = all(c["ok"] for c in verdict)
    print(json.dumps({"ok": ok, "port": args.port, "checks": verdict}, indent=2))
    raise SystemExit(0 if ok else 1)


def _up(base):
    try:
        urllib.request.urlopen(f"{base}/api/results", timeout=2).read()
        return True
    except (urllib.error.URLError, ConnectionError, OSError):
        return False


if __name__ == "__main__":
    main()
