"""Example Python sentinel plugin: the edit storm.

Four or more successful writes to the *same file* with no test run in between. The built-in
`same_file_edits_max` feature counts edits per file over the whole prefix, which cannot tell a
"write, test, write, test" rhythm (healthy) from "write, write, write, write" (an agent editing
blind). This plugin reads the event list, so it can.

Contract for a Python plugin:

    detect(features: dict, events: list[dict], harness: dict) -> list[dict]

returning the same shape the built-in detectors return: {id, label, severity, why, nudge}.
`severity` must be one of low / medium / high / critical. The optional module-level PATTERNS dict
below documents the ids for the UI catalogue. The file is hot-reloaded when its mtime changes, and
any exception it raises is caught and surfaced in the UI instead of killing the run.
"""

STORM = 4          # edits to one file, with no test run between the first and the last

PATTERNS = {
    "edit_storm": {
        "label": "Edit storm on one file",
        "severity": "high",
        "why": f"{STORM}+ writes to the same file with no test run in between.",
        "nudge": "You have rewritten the same file several times without running the tests once. "
                 "Run the tests on what you have now; edit again only after you have read the failure.",
        "action_hint": "nudge",
        "citation": "example python plugin, harnesslab/plugins/edit_storm.py",
    },
}

_EDIT_TOOLS = ("write_file", "edit_file")


def detect(features, events, harness):
    runs_by_file = {}          # path -> current unbroken edit streak (reset by any test run)
    worst = {}                 # path -> longest streak seen
    for e in events or []:
        if e.get("tests_passed") is not None:          # a test ran: every streak restarts
            runs_by_file = {}
            continue
        if e.get("tool") in _EDIT_TOOLS and e.get("status") == "ok":
            path = (e.get("args") or {}).get("path") or "?"
            runs_by_file[path] = runs_by_file.get(path, 0) + 1
            worst[path] = max(worst.get(path, 0), runs_by_file[path])
    hits = [(p, n) for p, n in worst.items() if n >= STORM]
    if not hits:
        return []
    path, n = max(hits, key=lambda t: t[1])
    p = PATTERNS["edit_storm"]
    return [{"id": "edit_storm", "label": p["label"], "severity": p["severity"],
             "why": f"{n} consecutive writes to {path} with no test run in between.",
             "nudge": p["nudge"]}]
