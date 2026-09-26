# Lab: Measuring a coding agent that will not hold still

**Duration:** 2 hours. **Format:** pairs. **Prerequisites:** Python 3.10+, a terminal, a browser, the OpenRouter key you received
(Anthropic or OpenAI keys, or any OpenAI-compatible endpoint, also work), and `pip install datasets` for exercise 7.
Everything also runs offline against pre-recorded runs, so a missing key never blocks you.

This morning's lecture argued that a single passing run tells you little about an agent. This afternoon you
will build the evidence yourself: run the same agent repeatedly, change only its harness, read the ledger
it leaves behind, calibrate a judge against an oracle, watch two distortions inflate a score, and then do the
same analysis on 500 real SWE-agent trajectories, and finish by turning the benchmark into an experiment.
You leave with a one-page evaluation report card generated from your own runs, a ranked list of the trajectory
features that predict failure on real data, and a variance decomposition that says how much of your result
was ever about the model.

Keep a shared notes file. Each exercise ends with questions; write two or three sentences per question.
We will discuss them in the last ten minutes.

## The platform: harnesslab

Every exercise below is a command-line script, and every one of them has a page in the platform. Use whichever
you prefer — they call the same functions in `harnesslab/core/analysis.py`, so the numbers agree by construction. The
pages are better for exploring; the scripts are better for the hand-in.

```bash
pip install -r harnesslab/requirements.txt
python -m harnesslab                       # http://127.0.0.1:8765
```

| page | what it is for | exercise |
|---|---|---|
| Command center | launch cells (model × harness × tasks × repeats), watch every span land live | 0 |
| Outcome | pass@k vs pass^k, task-bootstrap CIs, flip rate, and the cost-of-pass table | 1 |
| Harness lab | every (model, harness) cell side by side; which columns move; edit and save a harness | 2 |
| Trajectories | one run end to end: ledger, patch, risk curve; or two runs side by side | 1, 3 |
| Patterns | sets of runs over the action alphabet: shapes, transitions, divergence, regex queries | 3 |
| Sentinel | train the early-warning model, see how early it can tell, A/B a harness against itself | 3, 4 |
| Judge | calibrate a judge against the hidden-test oracle: κ, test–retest, position swap | 4 |
| Integrity | solution leakage, weak oracles, self-report vs oracle, Ochiai attribution | 5 |
| Experiment | the 2 × 2 factorial, variance shares, power curve, wrong-winner simulation | 8 |
| Data sources | import runs from Claude Code, Codex, OpenHands, SWE-agent or Inspect | 7 |
| Report card | the exercise-6 card, rendered and downloadable | 6 |

The **action alphabet** appears throughout: `L` list · `R` read/view · `F` search · `W` write · `E` edit ·
`T` run tests · `B` bash · `S` submit. A whole run becomes a short string like `LRTWTS`, which is what makes
the Patterns page's queries — and the tests you generate from them — possible.

---


> **Legacy console.** The earlier stdlib single-page app still ships (`python -m harnesslab.core.serve`, port 8766, `--export me.html`). harnesslab supersedes it; its remaining unique views (Patterns, Queries, the oracle chip) are being ported, after which it is removed.

## 0. Setup (0:00 – 0:12)

```bash
cd lab
python3 -m harnesslab run --provider mock --tasks t01_slugify --repeats 2 --out data/runs/mine -v
python -m harnesslab                       # the platform: http://127.0.0.1:8765
```

You should see two runs, each a handful of steps, ending in `PASS` or `fail`, and the console opening on the
Command center with the pre-recorded runs plus your `mine` directory. Look inside one run:

```bash
ls data/runs/mine/<run_id>/            # ledger.jsonl  summary.json  patch.diff  messages.json
python -m json.tool data/runs/mine/<run_id>/summary.json
```

Now the live agent. Either use Command center's launch
panel (provider, model, harness files, tasks, repeats; the API key is read from the terminal that started the
console) or a second terminal:

```bash
export OPENROUTER_API_KEY=...                      # the key you received (one per participant, $20 limit)
python -m harnesslab run --provider openrouter --model anthropic/claude-sonnet-5 \
    --harness harnesses/baseline.json,harnesses/no_test_tool.json \
    --tasks t01_slugify,t03_ratelimit,t06_injected_config,t07_cache_cleanup,t08_ambiguous_handler \
    --repeats 4 --out data/runs/live
```

Frontier models only: `anthropic/claude-sonnet-5`, `openai/gpt-5.6-sol`, `deepseek/deepseek-v4-pro`,
`z-ai/glm-5.3` are the recommended ids (about $0.05–0.15 per run; the cost per call is the exact charge
OpenRouter reports). `anthropic/claude-opus-5` costs about 2.5× and is worth one small comparison, not the
whole plan. `--provider anthropic` or `--provider openai` with your own keys also work. Both harnesses write
into `data/runs/live`, so one Command center shows both cells. Runs in flight are amber cells. Expected spend for the
whole afternoon: $5–10 of your $20. The runs take 5–10 minutes; do not wait for them.

While they run, skim `harnesslab/core/harness.py::run_task` (the control loop, 120 lines) and `harnesslab/core/tools.py`
(the tool surface and the policy). Find: where the stopping rule is; where a boundary event is recorded; what
`context_window` does. You will change all three later.

What is a harness, concretely? Everything in `HarnessConfig`: the system prompt, the list of tools, the
permission policy, the context-management rule, the step and token budgets, the temperature. The model is the
only thing not in it. In the console, open **Harness** with A = `baseline` and B = `no_test_tool`: the fields
that differ are highlighted, and there is exactly one.

## 1. The artefact under test does not hold still (0:12 – 0:25)

```bash
python exercises/ex1_variance.py                              # pre-recorded: 8 tasks x 10 repeats
python exercises/ex1_variance.py --results data/runs/live     # yours, once a few runs are in
```

Read the four sections in order: the outcome grid, pass@k versus pass^k, the confidence intervals, and the
patch/trajectory diversity table. Then the same in the console: in the header, switch the oracle chip from
hidden to strengthened and count the cells that change colour; on **Distribution**, read the per-task table
(Wilson next to Wald), the two curves, the flip map, and the path-diversity table.

Open a task with a flip (t03_ratelimit): two runs in the same cell with opposite verdicts. Click both cells on
Command center, then use **Compare two runs** in Trajectories; diff their patches:

```bash
grep '"t03_ratelimit"' data/runs/prerecorded_mock/index.jsonl | python -c "import sys,json; [print(json.loads(l)['run_id'], json.loads(l)['hidden_pass']) for l in sys.stdin]"
diff data/runs/prerecorded_mock/<passing_run>/patch.diff data/runs/prerecorded_mock/<failing_run>/patch.diff
```

Questions (in the script output). The one to argue about: Q3, which confidence interval belongs in a paper.

Stretch. `pass^k` for k = number of repeats is the fraction of tasks that never failed. Compute how many repeats
you would need to distinguish an agent with true per-task success 0.7 from one with 0.8, at the task counts
you actually have. (Binomial variance is enough; a simulation is better. The Experiment view computes the
two-proportion version for any two cells you click.)

## 2. The harness is a hidden variable (0:25 – 0:40)

```bash
python exercises/ex2_harness.py
python exercises/ex2_harness.py --results data/runs/live --baseline baseline --treatment no_test_tool
```

Six harnesses, one model. Look at which columns move: pass@1 barely; tokens per solve, verification rate,
boundary events, and exit reasons a lot. The paired bootstrap in section 3 is the honest comparison: tasks are
the unit, and with eight tasks the interval is wide. In the console, **Harness** shows the same table
("Columns that move") and the Δ_H panel with the per-task differences.

Now make your own harness. In the Harness view's editor (or by copying `harnesses/baseline.json` to
`harnesses/mine.json`) change one thing: drop `bash`, set `context_window` to 1, set `max_steps` to 4, rewrite
the system prompt, set `policy` to `permissive`. Predict, in writing, which columns will move. Then launch it
from Command center (5 repeats on the mock; 3 tasks × 3 repeats live if you have a key and time), and compare
A = `baseline`, B = yours:

```bash
python -m harnesslab run --provider mock --harness harnesses/mine.json,harnesses/baseline.json --repeats 5 --out data/runs/mine_mock
python exercises/ex2_harness.py --results data/runs/mine_mock --treatment <your id>
```

The point to take away. A leaderboard row that says "model M: 67%" is missing the harness. Vats & Golev (2026)
found a 40× spread in tokens per solved task across harnesses at equal pass rate. You just reproduced the
shape of that result in miniature.

## 3. Testing the trajectory, not just the patch (0:40 – 0:57)

```bash
python exercises/ex3_trajectories.py
HARNESSLAB_RESULTS=data/runs/prerecorded_mock HARNESSLAB_HARNESS=baseline   python -m unittest discover -s trajectory_tests -v 2>&1 | tail -25
HARNESSLAB_RESULTS=data/runs/prerecorded_mock HARNESSLAB_HARNESS=permissive python -m unittest discover -s trajectory_tests 2>&1 | grep FAIL | cut -c1-110
HARNESSLAB_RESULTS=data/runs/live python -m unittest discover -s trajectory_tests -v 2>&1 | tail -25
```

`trajectory_tests/test_conduct.py` contains three groups: safety policies (a failure is an incident), process
quality (conduct you want, but should it fail CI or be a reported rate?), and budgets (the run must be
measurable and affordable). Under the strict `baseline` harness the safety group passes and the process group
fails on some runs; under `permissive` the safety group fails too. Decide for each failing test whether it is a
policy or a metric, and write one line of justification.

Then read one trajectory end to end. In the console, **Trajectories** → open a run: the timeline lists every
span, the predicate table is evaluated on the ledger (verified after last edit, every edit eventually tested,
never edited a test file, no blocked action, eventually submitted, read before write, no identical call
repeated on an unchanged state), and clicking a failing predicate jumps to the span. Find the exact span where
a boundary event is recorded and the span where the agent decided to submit without re-running tests.

Then the set, not the run. **Patterns** shows the distinct action sequences with their pass rates, the
transition matrices for passing and failing runs, what happens at step k, and where the runs of each task
diverge (for t07, the branch that reaches for bash is the destructive-temptation branch). **Queries** lets you
write a predicate as a regex over the action string, for example `[WE](?!.*T)` (an edit never followed by a
test): it reports the matches, the pass rate inside and outside the match, Ochiai and a risk ratio with its
interval, and exports the query as a unittest with two methods, the gate and the rate.

Write one new trajectory test. The issue for t04 says "do not use the `csv` module"; t01 says "do not change
the public signature". HAL (Kapoor et al. 2026) found that failed agent runs violated a benchmark instruction
more than 60% of the time. Write a test that reads the final patch (`patch.diff` next to the ledger) and asserts
the instruction was respected. Run it on `live`.

Stretch. Turn "the destructive rate on t07 under strict policy is below 5%" into a test over the distribution of
runs. How many runs does it need before a pass means anything?

## 4. Calibrating an LLM judge (0:57 – 1:10)

```bash
python exercises/ex4_judge.py                                      # offline mock judge, biased on purpose
python exercises/ex4_judge.py --judge openrouter --model anthropic/claude-sonnet-5 --n 24 --repeats 2    # ~$0.50
```

The exercise judges patches that have an oracle (the hidden tests) so you can measure what you would otherwise
have to trust: raw agreement, Cohen's κ, test-retest consistency, position bias under swapping, and a
verbosity check. Compare the first two numbers. Zheng et al. (2023) reported "over 80% agreement"; Norman et
al. (2026) show the same judges sit at κ ≈ 0.4–0.5 once chance agreement is removed, and can be perfectly
repeatable while severely position-biased. The console's **Integrity** view applies the same κ machinery to
the oracles themselves (visible vs hidden, hidden vs strengthened).

Section 4 judges the process from the tool-call list and compares it with what the ledger says. Where the two
disagree on "verified" or "in scope", which do you believe?

Stretch (no oracle needed). Pad three patches with a large harmless comment block and re-judge. Does the verdict
move? That is a verbosity-bias test you can run on any judge, on any task, without ground truth.

## 5. Two distortions (1:10 – 1:20)

```bash
python exercises/ex5_distortions.py
```

(a) Leakage. t05's issue contains the fix in a maintainer comment. Its pass rate, step count, and
patch-to-issue similarity all stand out (**Integrity** → Task probes). SWE-bench+ found 32.67% of passed
patches had the solution in the issue text or comments.

(b) Weak tests. The same patches, re-graded with a strengthened suite (a superset of the hidden suite). Some
harnesses lose more than others and the ranking moves (**Integrity** → Does the ranking survive; or switch the
oracle chip and watch every view). UTBoost found 345 SWE-bench patches mislabelled as passing and 24.4% of
Verified leaderboard entries affected. Then read `tasks/t01_slugify/hidden_tests_strong/test_strong.py` and
decide whether you accept its unicode test as the intent of the issue. Strengthened oracles can be wrong too.

(c) Self-report. The agent's own last test run versus the oracle (the visible-vs-hidden matrix). Visible tests
are part of the task presentation, so they are part of the cell.

## 6. The report card (1:20 – 1:28)

```bash
python exercises/ex6_report_card.py --results data/runs/live --harness baseline > report_live.md
python exercises/ex6_report_card.py --harness baseline > report_mock.md
```

Six sections: the cell, outcome as a distribution, conduct, cost, integrity checks, and what the card does not
tell you. This is the lecture's closing recipe as code. The console's **Report card** generates the same
document from whatever filter is active (Copy Markdown). Read yours and answer:

1. Which single number in it would you have reported yesterday? Which three would you now insist on?
2. What is missing that BASTION-style evaluation would require (security of the produced code, provenance of
   the tasks, a versioned ledger schema shared across labs)? How would you add the first of those?

## 7. Five hundred real trajectories (1:35 – 1:50)

Everything so far ran on a mock agent or on your own small live runs. Now the same analysis on real data:
nebius/SWE-agent-trajectories, 80,036 SWE-agent runs on real GitHub issues, each labelled resolved or not
(CC-BY-4.0). You stream 500 of them.

```bash
pip install datasets
python exercises/ex7_real_trajectories.py --n 500 --to-console data/runs/nebius_500   # 1-3 minutes; also loads them into the console
python exercises/ex7_real_trajectories.py --n 300 --model swe-agent-llama-70b
python exercises/ex7_real_trajectories.py --offline               # the 500 real trajectories bundled in data/runs/real_swe_agent_500, no network
```

Section 1 reproduces the dataset card's resolved-vs-unresolved table on your sample (steps, files, lines,
submit rate, context exhaustion) next to the card's numbers for all 80,036. Section 2 gives the exit-status and
action-mix breakdown. Section 3 is the lecture's spectrum-based attribution: rows are runs, columns are binary
trajectory features (never verified after the last edit, no test run, repeated commands, context exhausted,
...), and each feature gets an Ochiai suspiciousness score, exactly the formula fault localisation uses on
statements. Section 4 prints one real unresolved trajectory as a list of actions. With `--to-console` the 500 real
trajectories become a results directory: open it in the console and the viewer, Patterns, Queries and
Attribution work on real SWE-agent runs with the same predicates (paths are not recoverable from the command
language, so read-before-write is n/a there; the rest holds).

Read the two rate columns, not only the score. A feature that is common in failures and successes ranks high
from base rate alone; that is SBFL's coincidental-coverage weakness, inherited intact.

Questions (in the script output). Q1 (cause, symptom, or harness artefact?) and Q3 (where does a step cap show
up on the report card?) are the ones to bring to the discussion.

Stretch. These trajectories were generated to train agents. Pick a feature that predicts failure and argue
whether filtering training data on it would teach the model to avoid the failure or to avoid the symptom. Then
look at `harnesslab/core/real_traj.py::classify_command` and improve the action classifier; check whether the ranking
changes.

## 8. From benchmark to experiment (1:50 – 1:58)

Every earlier exercise compared cells one pair at a time. A pairwise comparison cannot answer the question
that decides whether a result travels: *does this factor help everywhere, or only here?* That is the
interaction term, and only a factorial design has it.

```bash
python exercises/ex8_experiment.py
python exercises/ex8_experiment.py --results data/runs/prerecorded_mock --results2 data/runs/prerecorded_mock_weak   # 2 × 2 mock version
```

The default design is 8 × 2 on real recorded runs: eight models (`llma4se_live` + `live`, all collected
through OpenRouter) × two harnesses (`baseline`, `no_test_tool`), eight tasks as blocks — 688 runs, replayed
from disk, no key needed.

    Y_ijk = μ + α_i(model) + β_j(harness) + (αβ)_ij + u_k(task) + ε_ijk

Section 2 is the one to read. On these 688 real runs the model explains **12.2%** of the total sum of
squares, the harness **0.1%** and their interaction 1.8%; the task block takes 12.4%, cell × task 44.8% and
the run-to-run residual **28.8%**. Most of the variance in a resolve rate is *which task* and *which run*, not
which model or harness. A leaderboard reports the first two rows as though they were the whole story.

Section 3 gives the harness effect paired on tasks within each model. On the real runs it is not uniform:
removing the test tool costs Qwen3.8 27B 12.5 points (95% CI [−31.2, −1.6]) and leaves Claude Haiku 4.5 and
GPT-5-mini unchanged, while Gemini 2.5 Flash moves +25 points with an interval that still touches zero. Run the
2 × 2 mock version as the control: `mock-weak` is `MockProvider` with `strength = 0.65`, so its interaction is
additive *by construction*. If your fitted interaction there is far from zero, the design or the analysis is
wrong, not the agent.

In the platform, **Experiment** fits the same design for whatever is in the filter, with two additions: the
power curve (how many runs per arm you would need to detect a given difference) and a Monte-Carlo of how often
the wrong arm wins at the n you actually have. On a pair separated by five points with one repeat per task,
that error rate is rarely small.

**Questions.** Q1: your live model is one more level of the model factor — add it with `--results2` and say
whether the harness effect you measured in exercise 2 survives. Q2: you have budget for 200 runs. Spend them
on more tasks, more repeats, or more cells? Defend it with the variance shares, not intuition.

**Stretch.** Add a third factor of your own (temperature, `context_window`, a prompt variant) and run the
2 × 2 × 2. Before you look: write down which term you expect to dominate.

---

## Wrap-up (1:58 – 2:00)

Hand in: your notes file, `report_live.md` (or `report_mock.md`), the Ochiai table from exercise 7, and
the variance-share table from exercise 8.

On the 688 real runs the shares are: model 12.2%, harness 0.1%, interaction 1.8%, task 12.4%, cell × task
44.8%, residual 28.8%. What a leaderboard reports is the first two rows. The 2 × 2 mock version (command above)
gives model 2.1%, harness 0.7%, interaction ≈ 0, task 18.7%, cell × task 9.2%, residual 69.4% — same lesson.

Write down the sentence you would put in a paper: the cell, the paired Δ, the interval, the n. Then add your
live model as a third level and see whether the harness lines stay parallel.

## If something breaks

* `RuntimeError: OPENROUTER_API_KEY is not set`: export the key in the terminal running the command (or the
  one that started the console).
* HTTP 429/529: the runner retries with backoff; reduce `--repeats` or switch model.
* A model that never calls tools (exit `no_action`): try a different model; some small models are poor at tool
  use. The mock provider always works.
* `python -m unittest discover` finds no tests: run from the `lab/` directory.
* The console shows no runs: it reads `data/runs/*`; check the results chips at the top of the page.
* `pip install datasets` fails or there is no network: `python exercises/ex7_real_trajectories.py --offline`.
* Windows: use `set` instead of `export`, and forward slashes are fine in paths.
