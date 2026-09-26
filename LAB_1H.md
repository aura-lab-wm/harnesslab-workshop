# The lab in one hour — run sheet

> **Platform note (2026-09-08).** This run sheet now targets **harnesslab 0.3.0**, not the older
> `harnesslab.core.serve` console. Page names changed: Board → **Runs**, Comparison folded into
> **Harness lab**, Trajectories → **Explorer**. The Field is the console's own page now (**The Field**, second in
> the nav, and slide 2 of Present) — no separate file to open; it follows the console's results dir, oracle and theme.
> The lecture deck is now the console's own **Present** page (16 slides, follows this clock).
> Participants can skip setup entirely with the offline bundle: `python -m harnesslab --export lab.html`.


The handout is written for two hours (0:00–2:00). You have one. The cut is simple: setup is
compressed to nine minutes, exercises 1–3 are done together and are the whole lab, 4–6 are
a solo start in the last ten minutes, 7–8 are homework with the handout. Nothing below needs
a network or an API key; everything runs on the 640 pre-recorded runs.

| clock | block | what happens | what they must see before you move on |
|---|---|---|---|
| 0:00 | Open · 3 min · you only | `python3 -m harnesslab --no-browser` on your laptop, then http://127.0.0.1:8765/#present on the projector (the lecture deck; slide 2 is the cold open). Switch the oracle: 163 runs change verdict. Press **rank**, then **resample**: the leader changes in 50% of 2,000 task sets. Press **flags**: 295 runs fail a trajectory test, 165 of them pass the hidden oracle. One sentence: "you measure this yourselves in the next hour." | — |
| 0:03 | Setup · 9 min · everyone | `python3 --version` (3.10+) · `cd harnesslab` · `python3 -m harnesslab` — opens the console on **Command center**. Launch from there: model `mock` and harness `baseline` are preselected, click task chips until only `t01_slugify` stays highlighted, repeats `2`, results dir stays `mine`, press **Launch 4 runs → data/runs/mine**. Terminal alternative: `python3 -m harnesslab run --provider mock --tasks t01_slugify --repeats 2 --out data/runs/mine -v` | Four runs under **Live runs** ending PASS (two `baseline`, two `baseline`+sentinel — the A/B toggle is on); **Jobs** reads `finished · mine`; the results selector offers `mine · 4 runs`. Then click **The Field** in the left rail. **Do not leave setup until every screen shows this.** |
| 0:12 | Ex 1 together · 12 min | `python3 exercises/ex1_variance.py`. Read the table aloud: pass@1 0.66, pass^5 0.34 on the same 80 runs; the run-level CI [0.56, 0.76] against the task-level [0.46, 0.86], twice as wide. Then the console: Outcome → change the oracle chip → read the event card. | They can say why 0.66 and 0.34 describe the same 80 runs, and which interval is honest. |
| 0:24 | Ex 2 together · 12 min | `python3 exercises/ex2_harness.py`. baseline vs no_test_tool: pass@1 0.66 → 0.59, tokens per solve 9,023 → 4,939, verified 0.93 → 0.00; paired Δ −0.075, 95% [−0.30, +0.11], covers zero. Console: Harness lab, A = baseline, B = no_test_tool — one field differs. | They can name the one field that differs and say why the pass column is not the finding. |
| 0:36 | Ex 3 together · 12 min | `python3 exercises/ex3_trajectories.py`. baseline fails only process-quality tests (unverified submits); permissive lets one destructive shell through on t07 and also fails `test_tests_never_modified`. Console: Trajectories (Explorer) → open a run → the bill of materials: a passing run with "verified after last edit: no". | They can read one ledger and say what the score is blind to. |
| 0:48 | Solo start · 8 min | Assign by table: ex 4 (judge), ex 5 (distortions), ex 6 (report card). Each is one script and one console view. Show the Report card page once: that is the hand-in. | Each table has run its script once. |
| 0:56 | Wrap · 4 min | Hand-in: `report_mock.md` from the Report card, the disclosure card (Harness lab → copy Markdown), and their answers to ex 1 Q2, ex 2 Q1, ex 3 Q1. Ex 7 and 8 are homework with the handout; ex 7 needs `pip install datasets`. | — |

## Where the hour dies, and what to do about it tonight

Setup. Nine minutes only works if the repo is already on their machines. Send the setup block
above before lunch, with the repo as a zip that includes `data/runs/` (the pre-recorded runs are
what make everything else work offline). On a stock Mac the command is `python3`, not `python`;
the runner already resolves the interpreter itself, but the command they type must be `python3`.
If port 8765 is taken: `python3 -m harnesslab --port 8766 --no-browser`.

Live runs from the Field. On your laptop the Field's **run** panel (`n`) launches the runner through
`/api/launch` with the backends that exist today as presets: mock, mock-weak, rocco (local vLLM :8003),
Ollama Cloud glm-5.2:cloud and kimi-k3:cloud. The free presets pass `--price 0,0`, so the cost column
stays at zero instead of a fake default. The panel shows which local endpoints are answering before you
press launch. Fire a sweep at 0:12 (kimi-k3:cloud × baseline × 8 tasks × 2 is the unswept one) and let it
land during the exercises; the field grows a row and the log records every landing. This only works
from the machine running the backends — the projector page is your laptop, not theirs.

Live runs. The handout's OpenRouter block is optional and it is the one thing that can eat the
hour. If participants have keys, paste the live runner command at 0:12 as fire-and-forget and
never wait for it; if a few runs have landed by 0:36, `ex1_variance.py --results data/runs/live`
is a nice aside. If they do not have keys, skip it and say so — nothing in the hour depends on it.

The lab deck is 51 slides for a two-hour block. For one hour you need about fifteen: the title,
the recap, the setup steps, one slide per exercise 1–3 with "type this / you should see", the solo
menu, the wrap. Say the word and I cut that deck tonight; the slides already exist, this is a
selection, not a rewrite.

## Numbers you will say out loud (all 640 runs, hidden oracle unless stated)

pass@1 61.9%, task bootstrap [0.49, 0.76] · 86% of tasks flip inside a cell · pass^3 30.5% ·
visible → hidden changes 163 runs, hidden → strengthened changes 55, all pass → fail ·
mock · baseline alone: pass@1 66.3%, pass^5 0.34, 6 of 8 tasks flip · tight_budget − baseline
+6.3 pts, 95% [−5, +19], covers zero · the leader changes in 50% of 2,000 resampled task sets,
mean chance a system keeps its rank 38% · six of eight systems are indistinguishable from first.
