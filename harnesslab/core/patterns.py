"""Patterns across sets of trajectories, over the action alphabet.

One run becomes a string (`Trajectory.action_string`): L list · R read/view · F search ·
W write · E edit · T run tests · B bash · S submit. Everything here is a statement about
*sets* of runs — which action follows which, when the first edit lands, where runs of the
same task diverge, which shapes fail — because a single trajectory cannot tell you whether
a habit is associated with failure.

Used by the platform's Patterns page and by exercise 3.
"""
from __future__ import annotations
import math, re
from collections import Counter, defaultdict

ALPHABET = "LRFWETBS"
ACTION_LABEL = {"L": "list", "R": "read/view", "F": "search", "W": "write",
                "E": "edit", "T": "run tests", "B": "bash", "S": "submit"}


def wilson(c: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval. Correct at the extremes, where Wald says 0/5 = 0% ± 0%."""
    if not n:
        return (float("nan"), float("nan"))
    p = c / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def sequences(runs: list, outcome: str = "hidden_pass") -> list[dict]:
    """Group runs by their exact action string. The repeated shapes are the agent's habits."""
    groups = defaultdict(list)
    for t, row in runs:
        groups[t.action_string].append(row)
    out = []
    for seq, rows in groups.items():
        graded = [r for r in rows if r.get(outcome) is not None]
        c = sum(1 for r in graded if r.get(outcome))
        lo, hi = wilson(c, len(graded))
        out.append({"sequence": seq, "n": len(rows), "graded": len(graded),
                    "pass_rate": (c / len(graded)) if graded else None, "ci95": [lo, hi],
                    # over rows whose cost is known; a null cost is unknown, not $0
                    "mean_cost": (lambda c: sum(c) / len(c) if c else None)(
                        [r.get("cost_usd") or 0 for r in rows if not ("cost_usd" in r and r["cost_usd"] is None)]),
                    "mean_steps": (sum(r.get("steps") or 0 for r in rows) / len(rows)),
                    "example": rows[0].get("run_id")})
    return sorted(out, key=lambda d: -d["n"])


def transition_matrix(seqs: list[str], alphabet: str = ALPHABET) -> dict:
    """Row-normalised P(next action | current action). Rows that never occur stay zero."""
    counts = {a: {b: 0 for b in alphabet} for a in alphabet}
    for s in seqs:
        for i in range(len(s) - 1):
            if s[i] in counts and s[i + 1] in counts[s[i]]:
                counts[s[i]][s[i + 1]] += 1
    probs = {}
    for a in alphabet:
        tot = sum(counts[a].values())
        probs[a] = {b: (counts[a][b] / tot if tot else 0.0) for b in alphabet}
    return {"counts": counts, "probs": probs,
            "row_totals": {a: sum(counts[a].values()) for a in alphabet}}


def step_positions(seqs: list[str], max_steps: int = 24, alphabet: str = ALPHABET) -> list[dict]:
    """Share of runs doing each action at step k. Shows the shape of a typical run over time."""
    out = []
    for k in range(min(max_steps, max((len(s) for s in seqs), default=0))):
        live = [s for s in seqs if len(s) > k]
        c = Counter(s[k] for s in live)
        out.append({"step": k, "n_alive": len(live),
                    **{a: (c[a] / len(live) if live else 0.0) for a in alphabet}})
    return out


def first_index(seq: str, chars: str) -> int:
    for i, ch in enumerate(seq):
        if ch in chars:
            return i
    return -1


def divergence(runs: list, outcome: str = "hidden_pass") -> list[dict]:
    """Per task: the step at which runs of that task stop agreeing.

    Same task, same harness, same temperature — the step where the strings part company is
    where the run-to-run variance is actually born."""
    by_task = defaultdict(list)
    for t, row in runs:
        by_task[row["task_id"]].append((t.action_string, row))
    out = []
    for task, items in sorted(by_task.items()):
        seqs = [s for s, _ in items]
        if len(seqs) < 2:
            continue
        k = 0
        while all(len(s) > k for s in seqs) and len({s[k] for s in seqs}) == 1:
            k += 1
        graded = [r for _, r in items if r.get(outcome) is not None]
        c = sum(1 for r in graded if r.get(outcome))
        out.append({"task": task, "runs": len(items), "diverge_at": k,
                    "common_prefix": seqs[0][:k],
                    "distinct_sequences": len(set(seqs)),
                    "pass_rate": (c / len(graded)) if graded else None,
                    "branches": sorted(Counter(s[k] if len(s) > k else "·" for s in seqs).items(),
                                       key=lambda kv: -kv[1])})
    return out


def _edit_distance(a: str, b: str, cap: int = 400) -> int:
    a, b = a[:cap], b[:cap]
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cluster(seqs: list[str], k: int = 4, sample: int = 200) -> list[dict]:
    """Average-linkage agglomerative clustering on edit distance, reported by medoid.

    The medoid is a real run, not an average of runs, so 'the typical failing shape' is
    something you can go and open."""
    S = seqs[:sample]
    if len(S) < 2:
        return []
    idx = [[i] for i in range(len(S))]
    D = [[_edit_distance(S[i], S[j]) if i != j else 0 for j in range(len(S))] for i in range(len(S))]

    def link(c1, c2):
        return sum(D[i][j] for i in c1 for j in c2) / (len(c1) * len(c2))

    while len(idx) > k:
        best, bi, bj = None, 0, 1
        for i in range(len(idx)):
            for j in range(i + 1, len(idx)):
                d = link(idx[i], idx[j])
                if best is None or d < best:
                    best, bi, bj = d, i, j
        idx[bi] = idx[bi] + idx[bj]
        idx.pop(bj)
    out = []
    for members in sorted(idx, key=len, reverse=True):
        medoid = min(members, key=lambda i: sum(D[i][j] for j in members))
        out.append({"size": len(members), "medoid": S[medoid],
                    "members": [S[i] for i in members[:12]]})
    return out


# --------------------------------------------------------------------------- queries
def query(runs: list, pattern: str = "", outcome: str = "hidden_pass", **filters) -> dict:
    """Runs whose action string matches a regex, versus the rest.

    Reports the pass rate inside and outside the match plus two association measures. This is
    how a hunch ('it fails when it edits before reading') becomes a number, and then a test."""
    try:
        rx = re.compile(pattern) if pattern else None
    except re.error as e:
        return {"error": f"bad regex: {e}"}

    inside, outside = [], []
    for t, row in runs:
        if row.get(outcome) is None:
            continue
        ok = True
        for k, v in filters.items():
            if v in (None, ""):
                continue
            if k == "steps_min" and (row.get("steps") or 0) < int(v):
                ok = False
            elif k == "steps_max" and (row.get("steps") or 0) > int(v):
                ok = False
            elif k == "exit" and row.get("exit_reason") != v:
                ok = False
        if not ok:
            continue
        (inside if (rx and rx.search(t.action_string)) else outside).append(row)

    def rate(rs):
        return (sum(1 for r in rs if r.get(outcome)) / len(rs)) if rs else None

    ef = sum(1 for r in inside if not r.get(outcome))       # matched and failed
    ep = sum(1 for r in inside if r.get(outcome))           # matched and passed
    nf = sum(1 for r in outside if not r.get(outcome))
    np_ = sum(1 for r in outside if r.get(outcome))
    F = ef + nf
    ochiai = ef / math.sqrt(F * (ef + ep)) if F and (ef + ep) else 0.0
    r_in = ef / (ef + ep) if (ef + ep) else float("nan")
    r_out = nf / (nf + np_) if (nf + np_) else float("nan")
    risk = (r_in / r_out) if r_out else float("nan")
    return {"pattern": pattern, "matched": len(inside), "unmatched": len(outside),
            "pass_in": rate(inside), "pass_out": rate(outside),
            "ochiai": ochiai, "risk_ratio": risk,
            "fail_rate_in": r_in, "fail_rate_out": r_out,
            "examples": [r.get("run_id") for r in inside[:12]]}


def as_unittest(pattern: str, name: str = "no_such_shape", max_rate: float = 0.0) -> str:
    """Turn a query into an assertion for trajectory_tests/. A finding you cannot re-run is an
    anecdote; this is the step that makes it a regression test."""
    safe = re.sub(r"\W+", "_", name).strip("_") or "pattern"
    return f'''import unittest
from harnesslab.core.trajtest import load_runs

RESULTS = "data/runs/prerecorded_mock"
PATTERN = r"{pattern}"


class Test{safe.title().replace("_", "")}(unittest.TestCase):
    """Generated from a Patterns query. Fails when more than {max_rate:.0%} of runs match."""

    def test_{safe}(self):
        import re
        runs = load_runs(RESULTS)
        rx = re.compile(PATTERN)
        hits = [t for t in runs if rx.search(t.action_string)]
        rate = len(hits) / len(runs) if runs else 0.0
        self.assertLessEqual(
            rate, {max_rate!r},
            f"{{len(hits)}}/{{len(runs)}} runs match {{PATTERN!r}} "
            f"(examples: {{[t.action_string for t in hits[:3]]}})")


if __name__ == "__main__":
    unittest.main()
'''
