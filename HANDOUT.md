# Lab: Measuring a coding agent that will not hold still

**Duration:** 2 hours. **Format:** pairs. **Prerequisites:** Python 3.10+, a terminal, a browser, your own OpenRouter key
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

## The platform: TraceHarness

Every exercise below is a command-line script, and every one of them has a view in TraceHarness. Use whichever
you prefer — they call the same functions in `harnesslab/core/analysis.py`, so the numbers agree by construction.
TraceHarness is better for exploring; the scripts are better for the hand-in.

```bash
python -m pip install -e '.[real]'         # once; a codespace has done this for you
python -m harnesslab                       # TraceHarness: http://127.0.0.1:8765
```

| where in TraceHarness | what it is for | exercise |
|---|---|---|
| Analysis → **Run setup** | compose a cell (models × harnesses × tasks × repeats), see its cost and the smallest difference it can detect, launch it | 0, 2 |
| Analysis → **Outcomes** | pass@1 with a task-bootstrap 95% CI, pass@k against pass^k, the flip rate, every task's repeats in order | 1 |
| Analysis → **Family matrix**, **Comparison** | every (model, harness) cell side by side; each harness's paired Δ against baseline, task by task, with its interval | 2 |
| **Trajectories** → a run → **Compare with …** | every attempt of every task; one run end to end (how it failed, every step, Sentinel's risk curve, the evidence); two runs side by side from their first divergence | 1, 3 |
| Analysis → **Trajectory features** | sets of runs over the action alphabet: sequences, where a task's runs diverge, regex queries over the action string; on SWE-agent sets, the Ochiai ranking of trajectory features | 3, 7 |
| **Sentinel** | the early-warning model: how well it separates, what it catches at a threshold, how early, and the detectors that fire | 3, 4 |
| Analysis → **Judge & integrity** | the oracle against a stronger one (κ), integrity flags (leakage, weak tests, over-claiming), an LLM judge checked against the suites | 4, 5 |
| Analysis → **Experiment** | the model × harness fit, the cell grid with intervals, every within-model pairwise contrast | 8 |
| **Sources & capture** | import runs from Claude Code, Codex, Cursor, Gemini CLI, Qwen Code, OpenHands, SWE-agent or Inspect; download real SWE-agent trajectories | 7 |
| Analysis → **Report** | the exercise-6 card for the condition in focus, exported as Markdown or JSON | 6 |

Every analysis view sits under one sentence, *Asking [dataset] about [model] under [harness], graded by the
[suite] suite*, and each bracket is a menu. The test suite (visible · hidden · strengthened) is also the switch at
the top right: change it and every view re-grades.

The **action alphabet** appears throughout: `L` list · `R` read/view · `F` search · `W` write · `E` edit ·
`T` run tests · `B` bash · `S` submit. A whole run becomes a short string like `LRTWTS`, which is what makes
the queries in **Trajectory features** — and the tests you generate from them — possible.

---


## 0. Setup (0:00 – 0:12)

In the terminal, at the top of the repository:

```bash
python3 -m harnesslab run --provider mock --tasks t01_slugify --repeats 2 --out data/runs/mine -v
python -m harnesslab                       # TraceHarness: http://127.0.0.1:8765 (a codespace starts it for you)
```

You should see two runs, each a handful of steps, ending in `PASS` or `fail`, and TraceHarness opening on
**Datasets**, with your `mine` directory next to the pre-recorded ones. Look inside one run:

```bash
ls data/runs/mine/<run_id>/            # ledger.jsonl  summary.json  patch.diff  messages.json
python -m json.tool data/runs/mine/<run_id>/summary.json
```

Now the live agent. Either launch it from Analysis → **Run setup** (models, harness files, tasks, repeats; a real
model needs the key, from the terminal that started TraceHarness or under Settings → *The lab's key*) or from a
second terminal:

```bash
export OPENROUTER_API_KEY=...                      # your own key; give it a credit limit, e.g. $5
python -m harnesslab run --provider openrouter --model google/gemini-2.5-flash \
    --harness harnesses/baseline.json,harnesses/no_test_tool.json \
    --tasks t01_slugify,t03_ratelimit,t06_injected_config,t07_cache_cleanup,t08_ambiguous_handler \
    --repeats 4 --out data/runs/live
```

A cheap model is enough: `google/gemini-2.5-flash` cost about $0.004 per run on these tasks and
`google/gemini-2.5-flash-lite` about $0.001 (the cost per call is the exact charge OpenRouter reports), so the
whole afternoon costs a few dollars. A frontier model (`anthropic/claude-sonnet-5`, `openai/gpt-5.6-sol`, about
$0.05–0.15 per run) is worth one small comparison, not the whole plan. `--provider anthropic` or `--provider openai`
with your own keys also work. Both harnesses write
into `data/runs/live`, so its **Family matrix** shows both cells, and runs appear as they are written. The runs take
5–10 minutes; do not wait for them.

While they run, skim `harnesslab/core/harness.py::run_task` (the control loop, 120 lines) and `harnesslab/core/tools.py`
(the tool surface and the policy). Find: where the stopping rule is; where a boundary event is recorded; what
`context_window` does. You will change all three later.

What is a harness, concretely? Everything in `HarnessConfig`: the system prompt, the list of tools, the
permission policy, the context-management rule, the step and token budgets, the temperature. The model is the
only thing not in it. Compare two of them: `diff harnesses/baseline.json harnesses/no_test_tool.json`. Apart from
the id and the notes, exactly one field differs: `tools` (no `run_tests`, no `bash`).

## 1. The artefact under test does not hold still (0:12 – 0:25)

```bash
python exercises/ex1_variance.py                              # pre-recorded: 8 tasks x 10 repeats
python exercises/ex1_variance.py --results data/runs/live     # yours, once a few runs are in
```

Read the four sections in order: the outcome grid, pass@k versus pass^k, the confidence intervals, and the
patch/trajectory diversity table. Then the same in TraceHarness, on `prerecorded_mock`: Analysis → **Outcomes**
gives pass@1 with its interval, **Retries against consistency** sets pass@k against pass^k, and **Per task · repeats
in order** shows which tasks flip. Switch the test suite (top right) from *hidden* to *strengthened* and count the
tasks whose repeats change. The Wilson-versus-Wald table and the path-diversity table are in the script's
sections 3 and 4.

Open a task with a flip (t03_ratelimit): two runs in the same cell with opposite verdicts. In **Trajectories**,
open one of its failing squares and press **Compare with …**: the failed run next to a passing repeat, from the
first step where they differ. Then diff their patches:

```bash
grep '"t03_ratelimit"' data/runs/prerecorded_mock/index.jsonl | python -c "import sys,json; [print(json.loads(l)['run_id'], json.loads(l)['hidden_pass']) for l in sys.stdin]"
diff data/runs/prerecorded_mock/<passing_run>/patch.diff data/runs/prerecorded_mock/<failing_run>/patch.diff
```

Questions (in the script output). The one to argue about: Q3, which confidence interval belongs in a paper.

Stretch. `pass^k` for k = number of repeats is the fraction of tasks that never failed. Compute how many repeats
you would need to distinguish an agent with true per-task success 0.7 from one with 0.8, at the task counts
you actually have. (Binomial variance is enough; a simulation is better. Analysis → **Run setup** shows the
smallest difference a planned cell can detect, and **Experiment** lists every within-model contrast with its
interval.)

## 2. The harness is a hidden variable (0:25 – 0:40)

```bash
python exercises/ex2_harness.py
python exercises/ex2_harness.py --results data/runs/live --baseline baseline --treatment no_test_tool
```

Six harnesses, one model. Look at which columns move: pass@1 barely; tokens per solve, verification rate,
boundary events, and exit reasons a lot. The paired bootstrap in section 3 is the honest comparison: tasks are
the unit, and with eight tasks the interval is wide. In TraceHarness, Analysis → **Family matrix** puts the
harnesses side by side, and **Comparison** gives each one's paired Δ against baseline, task by task, with its interval.

Now make your own harness: copy `harnesses/baseline.json` to `harnesses/mine.json` (and set its `id` to `mine`),
then change one thing: drop `bash`, set `context_window` to 1, set `max_steps` to 4, rewrite
the system prompt, set `policy` to `permissive`. Predict, in writing, which columns will move. Then launch it
from Analysis → **Run setup**, which lists every file in `harnesses/` (5 repeats on the mock; 3 tasks × 3 repeats live if you have a key and time), and compare
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

Then read one trajectory end to end. In **Trajectories**, open a run. **How it failed** names the outcome, with
evidence links that jump to the exact events (the first edit, the last test run, the grade). **What it did** lists
every step beside Sentinel's risk curve and the detectors that fired, and every step opens its event: the model's
text, the tool it asked for, the raw span. Find the exact event where a boundary event is recorded and the step
where the agent submitted without re-running tests (Sentinel calls it `submit_without_verify`). The full list of
conduct predicates (verified after last edit, every edit eventually tested, never edited a test file, no blocked
action, eventually submitted, read before write, no identical call repeated on an unchanged state) is what the
trajectory tests above evaluate on every run.

Then the set, not the run. Analysis → **Trajectory features** shows the distinct action sequences with their
pass rates and where the runs of each task diverge (for t07, half the runs reach for bash, `B`, where the other
half run the tests: that is the destructive-temptation branch). **Query the action string** takes a predicate as
a regex over the action string, for example `[WE](?!.*T)` (an edit never followed by a test): it reports how many
runs match, the pass rate inside and outside the match, Ochiai and the risk ratio, and gives the query as a
unittest that fails when more runs match than you allow. The same from the terminal, where the JSON also holds the
transition matrices for passing and failing runs and what happens at each step:

```bash
python -m harnesslab patterns prerecorded_mock --harness baseline
python -m harnesslab patterns prerecorded_mock --harness baseline --query '[WE](?!.*T)'
python -m harnesslab patterns prerecorded_mock --harness baseline --query '[WE](?!.*T)' --unittest > trajectory_tests/test_edit_never_tested.py
```

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
repeatable while severely position-biased. Analysis → **Judge & integrity** applies the same κ machinery to the
oracles themselves (hidden vs strengthened), and checks an LLM judge against the suites once the lab's key is set.

Section 4 judges the process from the tool-call list and compares it with what the ledger says. Where the two
disagree on "verified" or "in scope", which do you believe?

Stretch (no oracle needed). Pad three patches with a large harmless comment block and re-judge. Does the verdict
move? That is a verbosity-bias test you can run on any judge, on any task, without ground truth.

## 5. Two distortions (1:10 – 1:20)

```bash
python exercises/ex5_distortions.py
```

(a) Leakage. t05's issue contains the fix in a maintainer comment. Its pass rate, step count, and
patch-to-issue similarity all stand out (Analysis → **Judge & integrity** → *Per task · hidden vs strengthened,
with probes*). SWE-bench+ found 32.67% of passed
patches had the solution in the issue text or comments.

(b) Weak tests. The same patches, re-graded with a strengthened suite (a superset of the hidden suite). Some
harnesses lose more than others and the ranking moves: switch the test suite (top right) to *strengthened* and
watch **Family matrix** and **Comparison** re-rank; **Judge & integrity** flags the weak hidden tests. UTBoost found 345 SWE-bench patches mislabelled as passing and 24.4% of
Verified leaderboard entries affected. Then read `tasks/t01_slugify/hidden_tests_strong/test_strong.py` and
decide whether you accept its unicode test as the intent of the issue. Strengthened oracles can be wrong too.

(c) Self-report. The agent's own last test run versus the oracle: the dataset overview's *Visible ≠ hidden* figure,
and the *over-claiming harness* flag in **Judge & integrity**. Visible tests
are part of the task presentation, so they are part of the cell.

## 6. The report card (1:20 – 1:28)

```bash
python exercises/ex6_report_card.py --results data/runs/live --harness baseline > report_live.md
python exercises/ex6_report_card.py --harness baseline > report_mock.md
```

Six sections: the cell, outcome as a distribution, conduct, cost, integrity checks, and what the card does not
tell you. This is the lecture's closing recipe as code. Analysis → **Report** generates the same card for the
condition in the Asking line, and **Export .md** downloads it. Read yours and answer:

1. Which single number in it would you have reported yesterday? Which three would you now insist on?
2. What is missing that BASTION-style evaluation would require (security of the produced code, provenance of
   the tasks, a versioned ledger schema shared across labs)? How would you add the first of those?

## 7. Five hundred real trajectories (1:35 – 1:50)

Everything so far ran on a mock agent or on your own small live runs. Now the same analysis on real data:
nebius/SWE-agent-trajectories, 80,036 SWE-agent runs on real GitHub issues, each labelled resolved or not
(CC-BY-4.0). You download 500 of them.

```bash
pip install datasets
python exercises/ex7_real_trajectories.py --n 500 --to-console data/runs/nebius_500   # seconds (one 85 MB file, cached); also loads them into the console
python exercises/ex7_real_trajectories.py --n 300 --model swe-agent-llama-70b
python exercises/ex7_real_trajectories.py --offline               # the 500 real trajectories bundled in data/runs/real_swe_agent_500, no network
```

Section 1 reproduces the dataset card's resolved-vs-unresolved table on your sample (steps, files, lines,
submit rate, context exhaustion) next to the card's numbers for all 80,036. Section 2 gives the exit-status and
action-mix breakdown. Section 3 is the lecture's spectrum-based attribution: rows are runs, columns are binary
trajectory features (never verified after the last edit, no test run, repeated commands, context exhausted,
...), and each feature gets an Ochiai suspiciousness score, exactly the formula fault localisation uses on
statements. Section 4 prints one real unresolved trajectory as a list of actions. With `--to-console` the 500 real
trajectories become a results directory, the same one **Sources & capture → Download a public sample** writes.
Open it in TraceHarness: Analysis → **Trajectory features** shows this ranking with example runs you can open, next
to the action sequences and queries, and every run replays in the run console (paths are not recoverable from the
command language, so read-before-write is n/a there; the rest holds). Step by step, with screenshots:
<https://traceharness.dev/guide/>.

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

In TraceHarness, Analysis → **Experiment** fits the same design for the dataset in the Asking line: the cell grid
with intervals and every within-model pairwise contrast. Before you spend anything, Analysis → **Run setup** shows
the smallest difference a planned cell can detect at its n. On a pair separated by five points with one repeat per
task, that number is rarely small enough.

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
  one that started TraceHarness, or paste it under Settings → *The lab's key*).
* HTTP 429/529: the runner retries with backoff; reduce `--repeats` or switch model.
* A model that never calls tools (exit `no_action`): try a different model; some small models are poor at tool
  use. The mock provider always works.
* `python -m unittest discover` finds no tests: run it from the top of the repository, the folder with `tests_agentlab/`.
* TraceHarness shows no runs: it reads `data/runs/*`; `python -m harnesslab --paths` prints the folder it uses.
* `pip install datasets` fails or there is no network: `python exercises/ex7_real_trajectories.py --offline`.
* Windows: use `set` instead of `export`, and forward slashes are fine in paths.
