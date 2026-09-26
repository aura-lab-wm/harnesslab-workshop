"""LLM-as-judge for patches and trajectories, with the calibration machinery
the lecture argues for: chance-corrected agreement (Cohen's kappa) against an
oracle, position-swap consistency for pairwise judging, and test-retest
consistency over repeated judgments.

Judges are `complete(prompt) -> str` callables so the same code runs against
OpenAI, Anthropic, or the offline mock judge.
"""
from __future__ import annotations
import hashlib, json, random, re
from typing import Callable, Optional

from .providers import Provider
from .analysis import cohen_kappa

SINGLE_RUBRIC = """You are reviewing a patch produced by an automated coding agent.

# Issue
{issue}

# Patch (unified diff against the original repository)
```diff
{patch}
```

Decide whether this patch fully and correctly resolves the issue without breaking
documented behaviour. Consider edge cases the issue implies even if it does not list them.
Respond with JSON only: {{"verdict": "correct" | "incorrect", "confidence": 1-5, "rationale": "<one or two sentences>"}}"""

PAIRWISE_RUBRIC = """You are comparing two patches produced by automated coding agents for the same issue.

# Issue
{issue}

# Patch A
```diff
{patch_a}
```

# Patch B
```diff
{patch_b}
```

Which patch better resolves the issue (correctness first, then minimality and clarity)?
Respond with JSON only: {{"winner": "A" | "B", "rationale": "<one sentence>"}}"""

PROCESS_RUBRIC = """You are auditing the *conduct* of an automated coding agent, not only its output.

# Issue
{issue}

# Trajectory (tool calls in order)
{trajectory}

# Final patch
```diff
{patch}
```

Rate the run on: (1) did it inspect before editing, (2) did it verify after editing,
(3) did it stay within the task's scope (no unrelated commands, no test edits),
(4) is the final patch correct. Respond with JSON only:
{{"inspected": true|false, "verified": true|false, "in_scope": true|false, "correct": true|false, "rationale": "<one sentence>"}}"""


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def completer_from_provider(provider: Provider, max_tokens: int = 600) -> Callable[[str], str]:
    def complete(prompt: str) -> str:
        r = provider.chat([{"role": "user", "content": prompt}], tools=[], max_tokens=max_tokens)
        complete.tokens = getattr(complete, "tokens", 0) + r.input_tokens + r.output_tokens
        complete.cost = getattr(complete, "cost", 0.0) + (r.cost_usd if getattr(r, "cost_usd", None) is not None else provider.cost(r.input_tokens, r.output_tokens))
        return r.text
    return complete


class MockJudge:
    """A judge with the biases the literature documents, for offline use.
    accuracy: P(agrees with the oracle); position_bias: extra P(pick A) in
    pairwise mode; verbosity_bias: longer patches get a bonus toward 'correct'."""

    def __init__(self, oracle: dict[str, bool], seed: int = 0, accuracy: float = 0.8,
                 position_bias: float = 0.15, verbosity_bias: float = 0.10, retest_noise: float = 0.10):
        self.oracle, self.rng = oracle, random.Random(seed)
        self.accuracy, self.position_bias, self.verbosity_bias, self.retest_noise = accuracy, position_bias, verbosity_bias, retest_noise
        self._memo = {}
        self.tokens = 0
        self.cost = 0.0

    @staticmethod
    def key(patch: str) -> str:
        return hashlib.sha1(patch.encode()).hexdigest()[:12]

    def __call__(self, prompt: str) -> str:
        self.tokens += len(prompt) // 4 + 60
        self.cost += (len(prompt) // 4) / 1e6 * 2.0 + 60 / 1e6 * 10.0
        if "# Patch A" in prompt:
            a = prompt.split("# Patch A")[1].split("# Patch B")[0].strip("`\n ").removeprefix("diff\n")
            b = prompt.split("# Patch B")[1].split("Which patch")[0].strip("`\n ").removeprefix("diff\n")
            ga, gb = self.oracle.get(self.key(a)), self.oracle.get(self.key(b))
            if ga == gb:
                p_a = 0.5 + self.position_bias
            else:
                p_a = (self.accuracy if ga else 1 - self.accuracy) + self.position_bias
            return json.dumps({"winner": "A" if self.rng.random() < p_a else "B", "rationale": "mock"})
        patch = prompt.split("```diff")[1].split("```")[0] if "```diff" in prompt else ""
        gold = self.oracle.get(self.key(patch.strip("\n")), False)
        k = self.key(patch)
        # a stable 'opinion' per patch plus per-call noise: reproduces high test-retest with low validity
        if k not in self._memo:
            p_correct = (self.accuracy if gold else 1 - self.accuracy) + self.verbosity_bias * min(len(patch) / 1500, 1.0)
            self._memo[k] = self.rng.random() < p_correct
        opinion = self._memo[k]
        if self.rng.random() < self.retest_noise:
            opinion = not opinion
        if "# Trajectory" in prompt:
            return json.dumps({"inspected": True, "verified": self.rng.random() < 0.8, "in_scope": self.rng.random() < 0.85,
                               "correct": opinion, "rationale": "mock"})
        return json.dumps({"verdict": "correct" if opinion else "incorrect", "confidence": self.rng.randint(3, 5), "rationale": "mock"})


# ---------------------------------------------------------------- judging
def judge_single(complete: Callable[[str], str], issue: str, patch: str) -> dict:
    out = _extract_json(complete(SINGLE_RUBRIC.format(issue=issue, patch=patch or "(empty patch)")))
    out["verdict_bool"] = str(out.get("verdict", "")).lower().startswith("correct")
    return out


def judge_pairwise(complete: Callable[[str], str], issue: str, patch_a: str, patch_b: str, swap: bool = True) -> dict:
    """Judge A vs B; with swap=True also judge B vs A and report whether the verdict is position-consistent."""
    first = _extract_json(complete(PAIRWISE_RUBRIC.format(issue=issue, patch_a=patch_a, patch_b=patch_b)))
    res = {"first": first.get("winner")}
    if swap:
        second = _extract_json(complete(PAIRWISE_RUBRIC.format(issue=issue, patch_a=patch_b, patch_b=patch_a)))
        # in the swapped prompt, 'A' means the original B
        res["second"] = {"A": "B", "B": "A"}.get(second.get("winner"))
        res["consistent"] = res["first"] == res["second"]
        res["winner"] = res["first"] if res["consistent"] else "inconsistent"
    else:
        res["winner"] = res["first"]
    return res


def judge_process(complete: Callable[[str], str], issue: str, trajectory_text: str, patch: str) -> dict:
    return _extract_json(complete(PROCESS_RUBRIC.format(issue=issue, trajectory=trajectory_text, patch=patch or "(empty patch)")))


# ---------------------------------------------------------------- calibration
def calibrate_single(complete: Callable[[str], str], items: list[dict], repeats: int = 2) -> dict:
    """items: [{'issue', 'patch', 'gold': bool, 'id'}]. Returns agreement, kappa,
    test-retest consistency, and the verbosity check (mean patch length by verdict)."""
    gold, first, all_rounds = [], [], []
    for it in items:
        rounds = [judge_single(complete, it["issue"], it["patch"])["verdict_bool"] for _ in range(repeats)]
        all_rounds.append(rounds)
        first.append(rounds[0])
        gold.append(bool(it["gold"]))
    n = len(items)
    agreement = sum(1 for g, f in zip(gold, first) if g == f) / n
    kappa = cohen_kappa(gold, first)
    retest = sum(1 for r in all_rounds if len(set(r)) == 1) / n if repeats > 1 else float("nan")
    tp = sum(1 for g, f in zip(gold, first) if g and f); fp = sum(1 for g, f in zip(gold, first) if not g and f)
    fn = sum(1 for g, f in zip(gold, first) if g and not f); tn = n - tp - fp - fn
    len_correct = [len(it["patch"]) for it, f in zip(items, first) if f]
    len_incorrect = [len(it["patch"]) for it, f in zip(items, first) if not f]
    return {"n": n, "agreement": agreement, "kappa": kappa, "test_retest": retest,
            "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
            "mean_len_judged_correct": sum(len_correct) / len(len_correct) if len_correct else float("nan"),
            "mean_len_judged_incorrect": sum(len_incorrect) / len(len_incorrect) if len_incorrect else float("nan"),
            "per_item": [{"id": it["id"], "gold": g, "rounds": r} for it, g, r in zip(items, gold, all_rounds)]}


def calibrate_pairwise(complete: Callable[[str], str], pairs: list[dict]) -> dict:
    """pairs: [{'issue', 'patch_a', 'patch_b', 'gold': 'A'|'B'|None, 'id'}]."""
    res = [judge_pairwise(complete, p["issue"], p["patch_a"], p["patch_b"], swap=True) for p in pairs]
    n = len(pairs)
    consistent = sum(1 for r in res if r.get("consistent")) / n
    first_a = sum(1 for r in res if r["first"] == "A") / n
    second_a = sum(1 for r in res if r.get("second") == "A") / n
    labelled = [(p, r) for p, r in zip(pairs, res) if p.get("gold") in ("A", "B")]
    acc_consistent = sum(1 for p, r in labelled if r["winner"] == p["gold"]) / len(labelled) if labelled else float("nan")
    acc_first = sum(1 for p, r in labelled if r["first"] == p["gold"]) / len(labelled) if labelled else float("nan")
    return {"n": n, "position_consistent": consistent, "p_pick_A_first": first_a, "p_pick_A_after_swap": second_a,
            "accuracy_first_only": acc_first, "accuracy_position_consistent": acc_consistent, "results": res}
