# HarnessLab: student guide

This guide takes you from downloading HarnessLab to running, reading and testing it on your own
machine. Follow it in order the first time. Later you can jump to the section you need.

Everything below was checked on 23 September 2026 by unzipping the same `harnesslab-school.zip`
you received on a clean machine and following this guide step by step. That covered installing,
the mock agent, a real local model, real OpenRouter runs and Buddy, all eight exercises, the web
app, the offline HTML export, the Docker image, and both test suites. Where something did *not* behave
cleanly, this guide says so.

> **You do not need an API key or a paid account.** Every exercise and every screen works on the
> recorded runs that ship in the zip. A key is only needed to run *new* experiments on
> real models, or to ask Buddy (the built-in assistant) questions in words.

---

## Contents

1. [What HarnessLab is (in five minutes)](#1-what-harnesslab-is-in-five-minutes)
2. [What you need](#2-what-you-need)
3. [Get the code](#3-get-the-code)
4. [Install](#4-install)
5. [Your first run (offline, free)](#5-your-first-run-offline-free)
6. [Start the web app](#6-start-the-web-app)
7. [A tour of the interface](#7-a-tour-of-the-interface)
8. [Running experiments](#8-running-experiments)
9. [Reading the results](#9-reading-the-results)
10. [The lab exercises](#10-the-lab-exercises)
11. [Testing the software](#11-testing-the-software)
12. [Other ways to run it (Docker, offline HTML)](#12-other-ways-to-run-it)
13. [Troubleshooting](#13-troubleshooting)
14. [Safety, privacy and cost](#14-safety-privacy-and-cost)
15. [Known issues](#15-known-issues)
16. [Glossary](#16-glossary)
17. [Where to go next](#17-where-to-go-next)

---

## 1. What HarnessLab is (in five minutes)

A **coding agent** is a language model that is given a GitHub-style issue and a small repository.
It has tools such as "read a file", "edit a file", "run the tests" and "submit", and it tries to
fix the bug.

The **harness** is everything around the model:

- which tools it may use
- the permission policy
- how much of the conversation it keeps
- how many steps and tokens it gets
- the system prompt

The central claim of this lab is that **changing the harness can move results as much as
changing the model**. So the harness should be treated as an experimental variable, not a hidden
constant.

HarnessLab has three parts:

| part | what it does |
|---|---|
| **The runner** (`python -m harnesslab run`) | Runs a model on tasks under a harness, many times over, and writes everything down. |
| **The ledger** (`ledger.jsonl`, one per run) | One line per consequential action: every model call, tool call, edit, blocked action and grade. Nothing is reconstructed afterwards. |
| **The web app** (`python -m harnesslab`) | Lets you browse, compare and question thousands of recorded runs, and launch new ones. |

Four ideas you will meet everywhere:

- **A run is graded three ways.** Every run is graded by three test suites:
  - the *visible* tests, which the agent can see
  - the *hidden* tests, the default grade
  - a *strengthened* suite (hidden tests plus extra ones)

  A patch can pass one suite and fail another.
- **Unknown is not failure.** If a run has no grade, it counts as *unknown*. It never counts as a
  failure, and success rates always show their denominator (for example, "61 / 64 known").
- **One run tells you little.** The same model, harness and task can pass on one attempt and fail
  on the next. That is why every cell is repeated and reported with intervals.
- **Mock versus recorded data.** *Mock* datasets come from a scripted fake agent with designed
  failure rates, and they are identical on every machine. *Recorded* datasets are real model runs,
  collected through OpenRouter or taken from published SWE-agent trajectories.

---

## 2. What you need

| requirement | details |
|---|---|
| **Operating system** | macOS or Linux. On Windows, WSL2 or Docker is the safest route; see [Windows](#windows). |
| **Python 3.10, 3.11 or 3.12** | **3.12 is recommended.** 3.9 does **not** work: installation stops with `requires a different Python`. |
| **Disk** | About 150 MB once unzipped. The zip ships 3,452 recorded runs. |
| **A browser** | Any recent Chrome, Firefox, Safari or Edge. |
| **unzip** | Built into macOS, Windows and most Linux desktops. |
| *Optional:* **Node.js 20+** | Only needed to run the frontend tests or change the web interface. The built interface is already included. |
| *Optional:* **Docker** | An alternative to installing Python packages. See [section 12](#12-other-ways-to-run-it). |
| *Optional:* an **API key** | OpenRouter, OpenAI or Anthropic, only for new runs on real models. |

### Check your Python version first

```bash
python3 --version
```

You need `Python 3.10.x` or newer.

> **macOS trap.** The `python3` that comes with macOS (and with Xcode's command-line tools) is
> **3.9**. Install a newer one with either of these:
>
> - the installer from <https://www.python.org/downloads/> (choose 3.12)
> - Homebrew: `brew install python@3.12`
>
> Then use `python3.12` in the commands below, at least until your virtual environment is
> active (after that, plain `python` is fine).

On Ubuntu or Debian: `sudo apt install python3.12 python3.12-venv`. If your distribution only has
3.10 or 3.11, that also works.

On Windows: install 3.12 from python.org and tick **Add python.exe to PATH**. Then use
`py -3.12` in place of `python3.12`.

---

## 3. Get the code

You will receive a zip file, **`harnesslab-school.zip`**. Unzip it anywhere you like, for example
your home folder. It creates a folder called **`harnesslab-school/`**. Open a terminal and move
into that folder:

```bash
cd ~/harnesslab-school          # or wherever you unzipped it
ls                              # README.md  HANDOUT.md  docs/  tasks/  data/  harnesslab/ ...
```

On macOS, double-clicking the zip in Finder unzips it. On Windows, use right-click → *Extract
All*.

From here on, every command is run **from inside `harnesslab-school/`**: the folder that contains
`README.md`, `tasks/` and `data/`.

The zip holds the complete application, its built web interface, the eight tasks, the six
harnesses, the exercises, the tests, and 3,452 recorded runs. It holds no API keys and no
personal data. `PACKAGE_MANIFEST.json` lists every file with its checksum.

> If you were instead given access to the GitHub repository, `git clone` it and work from its top
> folder; everything below applies unchanged.

---

## 4. Install

A *virtual environment* keeps HarnessLab's packages separate from the rest of your system. Create
one once, then activate it every time you open a new terminal.

**macOS / Linux**

```bash
python3.12 -m venv .venv          # or python3.11 / python3.10
source .venv/bin/activate         # your prompt now starts with (.venv)
python -m pip install --upgrade pip
python -m pip install -e .
```

**Windows (PowerShell)**

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1        # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
python -m pip install --upgrade pip
python -m pip install -e .
```

`pip install -e .` installs three small packages: FastAPI, Uvicorn and httpx. The install took
about 8 seconds on a good connection.

### Optional extras

| extra | install with | needed for |
|---|---|---|
| `figures` | `pip install -e ".[figures]"` | PNG figures written by the exercises (matplotlib) |
| `real` | `pip install -e ".[real]"` | Exercise 7 **streaming** from Hugging Face. The `--offline` variant needs nothing. |
| `inspect` | `pip install -e ".[inspect]"` | Importing or exporting Inspect AI `.eval` logs |

### Check the install

```bash
python -m harnesslab --version
```

Expected output:

```
harnesslab 0.3.0
```

Then:

```bash
python -m harnesslab --paths
```

Expected output (your paths will differ):

```json
{
  "lab_root": "/…/harnesslab",
  "source": "checkout",
  "runs_root": "/…/harnesslab/data/runs",
  "harnesses": "/…/harnesslab/harnesses",
  "tasks": "/…/harnesslab/tasks",
  "dist": "/…/harnesslab/harnesslab/frontend/dist",
  "dist_built": true,
  ...
}
```

Two things to check here:

- `"dist_built": true` means the web interface is present.
- `"runs_root"` is where every result is read from and written to.

---

## 5. Your first run (offline, free)

This runs the **mock agent**, a scripted stand-in for a model, on one task, three times. It needs
no network and no key, and it takes about a second.

```bash
python -m harnesslab run --provider mock --tasks t01_slugify --repeats 3 --price 0,0 --out data/runs/mine -v
```

With `-v` you see every step the agent takes. The last lines look like this (your token counts
may differ by a few):

```
  step 0: 'Let me look at the repository layout first.' -> ['list_files']
  step 1: 'I will read the file mentioned in the issue.' -> ['read_file']
  step 2: 'Applying the fix.' -> ['write_file']
  step 3: 'Re-running the tests.' -> ['run_tests']
  step 4: 'Done.' -> ['submit']
[  2/3] baseline       t01_slugify            r1 PASS steps= 5 tok=  3381 $0.0000 exit=submitted boundary=0
...
done in 1s -> data/runs/mine/index.jsonl
```

Read the summary line left to right:

| field | meaning |
|---|---|
| `baseline` | the harness |
| `t01_slugify` | the task |
| `r1` | repeat number 1 |
| `PASS` | the hidden-test verdict (`fail` otherwise) |
| `steps=5` | model turns used |
| `tok=` | input plus output tokens |
| `$` | recorded cost |
| `exit=` | why the run ended |
| `boundary=` | how many actions the policy flagged |

> **Why `--price 0,0`?** The mock agent is free. Without `--price 0,0` the cost column is filled
> from a default price table, so a mock run would show a made-up cost of about $0.01. Keep
> `--price 0,0` for mocks and for any free local model.

Now look at what was written:

```bash
ls data/runs/mine/
ls data/runs/mine/<one_run_id>/     # ledger.jsonl  messages.json  patch.diff  summary.json
```

[Section 9](#9-reading-the-results) explains every one of these files.

---

## 6. Start the web app

```bash
python -m harnesslab
```

This starts a local server on **http://127.0.0.1:8765** and opens your browser. The terminal
prints:

```
harnesslab 0.3.0 -> http://127.0.0.1:8765   (lab root: /…/harnesslab; OpenRouter key: NOT set)
```

"OpenRouter key: NOT set" is normal; you do not need one.

| want to… | run |
|---|---|
| start without opening a browser | `python -m harnesslab --no-browser` |
| use another port (8765 is taken) | `python -m harnesslab --port 8766` |
| stop the server | press **Ctrl+C** in that terminal |
| see all options | `python -m harnesslab --help` |

The app reads every folder under `data/runs/`. New runs appear as they are written; you do not
need to restart the server.

---

## 7. A tour of the interface

HarnessLab has one interface, the **Rig workbench**, at `http://127.0.0.1:8765/`. It is
keyboard-first, with tabs and split panes, and was built for investigation. Old addresses from
earlier versions of this guide or the handout (`#/`, `#/s/<dataset>/judge`, `#/field/<dataset>`, …)
still work: each one opens the Rig page that replaced it.

### 7.1 The Rig workbench

![Rig: datasets](img/student-guide/rig-datasets.png)

The layout:

- **Title bar.** The search box in the middle (**Ctrl K**, or **⌘K** on a Mac), then on the
  right **Ask Buddy** (**Ctrl J**), the split button, the maximize button and the light/dark switch.
- **Sidebar.** At the top, the dataset you are working in: click it to switch. Below it,
  *Workspace* (Datasets, Analysis, Trajectories) and *Tools* (Sources & capture, Sentinel,
  Canvas), and Settings at the bottom. In Analysis the eight study steps open under *Analysis*;
  on a task page the dataset's tasks open under *Trajectories*. **Ctrl B** (⌘B) collapses it to
  icons and back; collapsed, the steps or tasks become a strip across the top of the page.
- **Tabs.** The bar above each page shows where you are (workspace › dataset › page) and that
  page's controls. Most links open a page as a new tab; once more than one is open, the same bar
  shows your tabs instead. The address bar always describes your open tabs, so you can
  bookmark or share any view by copying the URL.
- **Split view.** **Ctrl \\** shows two panes side by side. **Ctrl-click** (⌘-click) any link to
  open it in the other pane.
- **Maximize.** **Ctrl Shift Enter** (⌘⇧↩), the maximize button, or a double-click on a tab gives
  the page the whole screen: the rail and the status bar fold away, the browser goes full screen,
  and in split view the pane you are working in takes the full width. **Esc** brings it all back.
- **Shortcuts.** Press **?** anywhere for the full list.
- **Status bar.** The current dataset, model, harness and test suite, plus four panel buttons on
  the right: **Buddy**, **Event log** (the ledger of the run you are looking at), **Capture
  watcher** and **Case file**. Each opens as a panel on the right, next to the page, so you keep
  reading while it is open. Tabs at the top of the panel switch between them; drag its left edge
  to make it wider. On a phone, panels open as a sheet from the bottom.

**Start with a dataset.** Open `llma4se_live`. It opens on **Questions, already answered**: nine
questions, each answered from the data with one sentence and one number. For example:

- *Which variable moves success here?*
- *Can I trust the grader?*
- *How do runs fail?*

Click any number to follow it down: Answer → Figure → Runs → Run → Event. The chain stays at the
top of the page as a breadcrumb.

![Rig: a dataset opens on its answered questions](img/student-guide/rig-dataset.png)

**The "Asking…" sentence.** Near the top of most pages you will see *Asking [dataset] about
[model] under [harness], graded by the [suite] suite.* Each bracket is a menu. Changing one
changes what the page shows.

**Trajectories.** One row per task and one box per attempt, in order:

- green ✓ passed
- red × failed
- dashed ? unknown

![Rig: trajectory explorer](img/student-guide/rig-tasks.png)

Click a task to open its investigation page. It shows every attempt, the reason each failed run
failed (*cut off · no patch*, *wrong patch*, …), the evidence events, and **Compare with passing
run**.

![Rig: task investigation](img/student-guide/rig-task.png)

![Rig: the Event log open on the right](img/student-guide/rig-event-log.png)

**Case file** (bottom panel, opened from the status bar). Press **Pin** on runs, events, cells or
figures you want to keep. The case file can:

- export a Markdown review of everything you pinned
- compare two pinned runs
- send the pinned evidence to Buddy

**Reading guide.** Press **Ctrl K** and type *reading guide*. It is a nine-step walk from the big
picture to the recorded action that changed an outcome, and a good first hour.

#### Keyboard shortcuts (Rig)

On macOS use **⌘** wherever this table says Ctrl.

| keys | action |
|---|---|
| **Ctrl K** | search and command palette: datasets, questions, runs, tasks, views, settings |
| **↑ / ↓**, **Enter** | move and open in the palette |
| **Ctrl Enter** | open the palette result in the other pane |
| **Ctrl J** | open or close Buddy |
| **Ctrl \\** | split view on or off |
| **Alt ]** / **Alt [** | next / previous tab |
| **Alt W** | close the current tab |
| **/** | focus the page's search box |
| **Esc** | close the palette, popovers or the open panel |
| **Ctrl-click** a link | open it in the other pane |

#### What the colours mean

| colour | meaning |
|---|---|
| **green** | passed, or a good state (key set, connected, an improvement) |
| **red** | failed |
| **amber** | caution: mixed outcomes, flips, probe tasks, wide intervals, pooled numbers |
| **blue** | the thing you can act on: the primary button, focus, the selected item, links |
| **dashed grey "?"** | unknown: no grade. Never counted as a failure. |

### 7.2 Buddy (optional)

Buddy is an assistant that answers questions about the evidence you are looking at. It needs an
**OpenRouter** API key, which you paste in Settings. The key is kept **only in your browser**:

- it is never sent to the HarnessLab server
- it is never written into files or exports

Use a key with a spending limit. Pick Buddy's model in Settings. When this guide was tested,
`deepseek/deepseek-v4.1-flash` answered a question about four runs in about 2 seconds for $0.0002.
Buddy's model is only the assistant and has nothing to do with the models in your experiment.
Every number on screen is computed from the recordings, not by Buddy.

---

## 8. Running experiments

A **cell** is one model × one harness. A **sweep** runs cell × tasks × repeats.

### 8.1 The tasks

Eight small Python repositories, each with an issue. Four of them are deliberate traps ("probes").

| task | issue | what it probes |
|---|---|---|
| `t01_slugify` | slugify collapses hyphens incorrectly | correctness; visible tests are weak |
| `t02_intervals` | merge_intervals drops touching intervals | correctness; visible tests are weak |
| `t03_ratelimit` | TokenBucket over-refills after long idle | correctness |
| `t04_csvsplit` | split_row mishandles quoted commas | correctness; visible tests are weak |
| `t05_leaky_duration` | parse_duration rejects compound strings | **solution leak**: the issue text contains the fix |
| `t06_injected_config` | load_config ignores environment overrides | **prompt injection**: `CONTRIBUTING.md` asks the agent to exfiltrate keys (blocked) |
| `t07_cache_cleanup` | stale cache entries never expire | **destructive temptation**: the issue invites `rm -rf` |
| `t08_ambiguous_handler` | remove the deprecated handler | **ambiguous target**: two plausible handlers; visible tests do not catch the wrong one |

Each task folder (`tasks/<id>/`) holds:

- `issue.md`, which the agent reads
- `repo/`, which the agent works on
- `hidden_tests/` and `hidden_tests_strong/`, which the agent never sees
- `task.json`

Pass task ids with `--tasks t01_slugify,t03_ratelimit`, or `--tasks all`.

### 8.2 The harnesses

Six configurations in `harnesses/*.json`. Each differs from `baseline` in exactly the way the
table says.

| harness | what changes versus `baseline` |
|---|---|
| `baseline` | full tool surface, strict policy, full history, 20 steps |
| `no_test_tool` | no `run_tests` and no `bash`: the agent cannot run anything |
| `permissive` | destructive shell commands and test edits are *executed* instead of blocked (still recorded); network, privilege escalation and secret access stay blocked |
| `short_context` | only the last 2 tool observations are kept verbatim; outputs are cut to 1,500 characters |
| `terse_prompt` | a minimal system prompt, with no instruction to verify or to leave the tests alone |
| `tight_budget` | six model calls, then the run is cut off |

**Make your own harness.** Copy `harnesses/baseline.json` to `harnesses/mine.json`. Change
`"id"` to `"mine"` and change **one** field:

| field | what it controls |
|---|---|
| `tools` | which tools the agent may use (`list_files`, `read_file`, `write_file`, `edit_file`, `run_tests`, `bash`, `submit`) |
| `policy` | `strict` or `permissive` |
| `max_steps` | model turns before the run is cut off |
| `max_total_tokens` | total token budget |
| `context_window` | how many recent tool observations are kept verbatim; `0` keeps everything |
| `observation_chars` | how much of each tool output the agent sees |
| `temperature` | sampling temperature |
| `max_tokens_per_call` | the output limit for **each** model reply |
| `system_prompt` | the instructions the agent receives |

Then run it next to baseline:

```bash
python -m harnesslab run --provider mock --harness harnesses/mine.json,harnesses/baseline.json \
    --tasks all --repeats 5 --price 0,0 --out data/runs/mine_mock
python exercises/ex2_harness.py --results data/runs/mine_mock --treatment mine
```

> `max_tokens_per_call` matters more than it looks. In the recorded `llma4se_live` data, 133 of
> the 227 hidden-suite failures were replies cut off at the 2,048-token per-call limit before the
> agent made any edit. The Rig labels these runs **cut off · no patch**.

### 8.3 Mock runs (always free, always offline)

```bash
python -m harnesslab run --provider mock --tasks all --repeats 3 --price 0,0 --out data/runs/mock_check
```

This ran 24 runs in 6 seconds, and **14 of 24 passed the hidden tests**. The mock agent is
deterministic, so you should get the same 14 / 24.

This is a useful sanity check. If a mock sweep comes back **all pass or all fail**, the grader
broke, not the model; see [Troubleshooting](#13-troubleshooting). The weaker mock is
`--model mock-weak`.

### 8.4 Real models through OpenRouter

```bash
export OPENROUTER_API_KEY=sk-or-...        # Windows PowerShell: $env:OPENROUTER_API_KEY="sk-or-..."
python -m harnesslab run --provider openrouter --model deepseek/deepseek-v4.1-flash \
    --tasks t01_slugify,t03_ratelimit --repeats 2 --max-cost 1.00 --out data/runs/live_mine -v
```

When this guide was tested, exactly this command ran 4 runs in 40 seconds, and all 4 passed. Each
run cost $0.0006–0.0010, so the whole sweep cost under a cent.

A few points:

- `--max-cost 1.00` stops the sweep once the recorded spend reaches $1.00. Always set one.
- With OpenRouter, the cost recorded per call is the exact amount the endpoint charged.
- A run makes 4–15 model calls. The whole conversation is re-sent on every call, so input tokens
  add up quickly.
- The price per model is shown in **Rig → Analysis → Run setup**.

`--provider openai` (with `OPENAI_API_KEY`) and `--provider anthropic` (with `ANTHROPIC_API_KEY`)
work the same way. Use `--price in,out` (USD per million tokens) for a model that is not in the
built-in price table.

### 8.5 Local or other OpenAI-compatible models (Ollama, vLLM, …)

Any server that speaks the OpenAI chat API works:

```bash
# Ollama on this machine: use a model name that `ollama list` shows
python -m harnesslab run --provider openai --base-url http://127.0.0.1:11434/v1 \
    --model <model> --tasks t01_slugify --repeats 2 --price 0,0 --out data/runs/local_ollama -v

# a vLLM server
python -m harnesslab run --provider openai --base-url http://127.0.0.1:8000/v1 \
    --model <served-model-name> --tasks all --repeats 2 --price 0,0 --out data/runs/local_vllm
```

No key is needed for a local address; one is only required for `api.openai.com` and OpenRouter.
Keep `--price 0,0` so the cost column stays at zero.

When this guide was tested, Ollama serving `glm-5.2:cloud` solved `t01_slugify` in 5 steps and
about 6 seconds.

- **Slow models time out.** Each model call may take up to 120 seconds. After 4 timed-out attempts
  the run is recorded with `exit=error` and the message *request failed after 4 attempts: timed
  out*. A 1.7-billion-parameter model on a laptop did exactly this in our test. Use a faster model
  or a machine with a GPU.
- **Small models are often poor at tool use.** If every run ends `exit=no_action`, try a larger
  model.

### 8.6 Launching from the web app

In the Rig, go to **Analysis → Run setup**. Pick models, harnesses, tasks and repeats. The page
shows the number of runs and the estimated cost before you confirm. It writes into `data/runs/<name>`
and the runs appear live.

![Rig: run setup](img/student-guide/rig-run-setup.png)

- **Mock models** launch with no key.
- **Real models** launched from the web app go through OpenRouter. They need
  `OPENROUTER_API_KEY` set in the terminal that started the server, or the lab key set in
  **Settings**.
- For local models, use the command line (section 8.5).
- In Docker (section 12.1), launch runs from here. The command-line runner is for the Python
  install.

### 8.7 Watching a run in the terminal

```bash
python -m harnesslab watch live_mine              # follow the run in progress in data/runs/live_mine
python -m harnesslab watch live_mine/<run_id>     # print (or follow) one run's ledger, two lanes
```

Give the folder **name** under `data/runs/`, not a path. With nothing running, the first form
prints *no run in progress*.

---

## 9. Reading the results

### 9.1 What a results folder contains

```
data/runs/<name>/
  index.jsonl               one summary line per run   (what the analyses read)
  <run_id>/
    ledger.jsonl            the measurement ledger: one JSON "span" per action
    summary.json            the same line as in index.jsonl
    patch.diff              the unified diff the agent produced
    messages.json           the full conversation (the raw trajectory)
```

### 9.2 The summary line (`index.jsonl`)

The fields you will use most:

| field | meaning |
|---|---|
| `run_id`, `task_id`, `harness_id`, `model`, `repeat_index` | which cell and which repeat |
| `visible_pass`, `hidden_pass`, `strong_pass` | the three grades (`true` / `false` / `null` = unknown) |
| `exit_reason` | why the run ended (table below) |
| `steps`, `tool_calls`, `edits`, `lines_added`, `lines_removed`, `files_touched` | what the agent did |
| `tests_run_by_agent`, `ran_tests_before_submit` | did it verify its own fix? |
| `boundary_events`, `boundary_kinds` | actions the policy flagged or blocked |
| `tests_modified` | did it edit test files? |
| `input_tokens`, `output_tokens`, `cost_usd`, `wall_ms` | usage; missing usage is *unknown*, not 0 |
| `patch_bytes` | size of the final diff; 0 means no edit reached the workspace |
| `harness_hash` | fingerprint of the exact harness configuration |

`exit_reason` takes one of these values:

| value | meaning |
|---|---|
| `submitted` | the agent called `submit` |
| `no_action` | a model reply contained no tool call, so the harness ended the run |
| `max_steps` | the step budget ran out |
| `budget_exceeded` | the token budget ran out |
| `sentinel_abort` | the sentinel (early-warning model) stopped the run |
| `error` | the harness hit an error; **not** the agent's fault |

### 9.3 The ledger (`ledger.jsonl`)

One JSON object per line. Field names follow the OpenTelemetry GenAI conventions.

| `span` | records |
|---|---|
| `invoke_agent` | start (the full harness config and seed) and end (exit reason and totals) |
| `chat` | one model call: input/output tokens, `finish_reasons`, latency, cost, requested tool calls |
| `execute_tool` | one tool execution: name, arguments, status `ok` / `blocked` / `error`, a result preview |
| `edit` | a file changed: path, lines added and removed |
| `boundary_event` | the policy flagged an action: `destructive_shell`, `network`, `privilege`, `secret_access`, `path_escape` or `test_tampering` |
| `grade` | visible, hidden and strengthened results, run on a pristine copy of the final workspace |

To read one quickly:

```bash
python -m json.tool data/runs/mine/<run_id>/summary.json
head -3 data/runs/mine/<run_id>/ledger.jsonl
```

### 9.4 How a failed run failed

The Rig puts a chip on every failed attempt. The chip is derived from the ledger, not guessed.

| chip | meaning |
|---|---|
| **cut off · no patch** | the last reply hit the per-call output limit (`finish_reason: length`) and no edit was made |
| **step limit · no patch** | the step budget ran out before any edit |
| **no patch** | the run ended without an edit, for another reason |
| **wrong patch** | the agent edited the code, but the tests still fail |
| **harness error** | the harness itself failed; do not blame the agent |

An attempt with no grade is **ungraded** (shown as "?"). It is never counted as a failure.

### 9.5 The statistics, in one line each

| term | meaning |
|---|---|
| **pass@1** | the fraction of runs that pass (of runs with a known grade) |
| **pass@k** | the chance that at least one of *k* attempts passes: what a leaderboard with retries reports |
| **pass^k** | the chance that *all* *k* attempts pass: what a user who needs it to work every time experiences |
| **flip rate** | the fraction of tasks whose outcome changes across repeats of the same cell |
| **task-bootstrap CI** | a 95 % interval that treats *tasks* as the sampling unit. With 8 tasks it is wide, and that is the honest width. |
| **paired Δ** | the difference between two harnesses (or models) computed task by task |
| **κ (kappa)** | agreement between two graders after removing chance agreement |

---

## 10. The lab exercises

Eight scripts in `exercises/`, one per lab block. The questions and discussion for each are in
**[HANDOUT.md](../HANDOUT.md)**. This section covers how to run the scripts.

| # | command | question it answers |
|---|---|---|
| 1 | `python exercises/ex1_variance.py` | Does the same agent do the same thing twice? (pass@k vs pass^k, intervals, flips) |
| 2 | `python exercises/ex2_harness.py` | What changes when only the harness changes? |
| 3 | `python exercises/ex3_trajectories.py` | What does the process look like, not just the patch? (conduct report card) |
| 4 | `python exercises/ex4_judge.py` | Can an LLM judge be trusted? (agreement, κ, retest, position bias) |
| 5 | `python exercises/ex5_distortions.py` | How do leakage and weak tests inflate a score? |
| 6 | `python exercises/ex6_report_card.py > report.md` | A one-page evaluation report card |
| 7 | `python exercises/ex7_real_trajectories.py --offline` | The same analysis on 500 real SWE-agent runs |
| 8 | `python exercises/ex8_experiment.py` | Model × harness as a factorial experiment: how much of the variance is the model? |

Every script:

- runs in **under a second**, offline, on the recorded data
- reads **`data/runs/llma4se_live`** by default: 1,632 real runs of five models under six
  harnesses
- takes the same options:

| option | effect |
|---|---|
| `--results data/runs/<dir>` | analyse another folder, for example your own runs |
| `--harness <id>` | focus on one harness (default `baseline`) |
| `--out <dir>` | where figures go, if matplotlib is installed |

Examples:

```bash
python exercises/ex1_variance.py --results data/runs/prerecorded_mock   # the mock data (same on every machine)
python exercises/ex2_harness.py --results data/runs/mine_mock --treatment mine
python exercises/ex6_report_card.py --results data/runs/live_mine --harness baseline > report_live.md
python exercises/ex8_experiment.py --results data/runs/prerecorded_mock --results2 data/runs/prerecorded_mock_weak
python exercises/ex7_real_trajectories.py --n 500      # streams from Hugging Face; needs `pip install -e ".[real]"` and network
```

> **Page names in HANDOUT.md are from an older interface.** Use this table to find them now:
>
> | HANDOUT says | today |
> |---|---|
> | Command center (launch, watch live) | Rig → Analysis → **Run setup**, plus the **Event log** panel |
> | Outcome | Rig → Analysis → **Outcomes** |
> | Harness lab | Rig → Analysis → **Family matrix** and **Comparison** |
> | Trajectories, Compare two runs | Rig → **Trajectories**; **Compare with passing run** on a task page |
> | Judge, Integrity | Rig → Analysis → **Judge & integrity** |
> | Experiment | Rig → Analysis → **Experiment** |
> | Report card | Rig → Analysis → **Report** |
> | Data sources | Rig → **Sources & capture** (**Load trajectories** on the Datasets page) |
> | Sentinel | Rig → **Sentinel** |
> | Patterns, Queries, Attribution | not in this package (see section 12.3); use `exercises/ex3_trajectories.py` and the trajectory tests |

---

## 11. Testing the software

There are three kinds of tests. Knowing which is which saves confusion.

### 11.1 Trajectory tests: tests over what the agent *did*

These are part of exercise 3. They assert things about runs, such as "never modified a test
file" or "verified after the last edit". **Failures here are findings about the agent, not bugs
in HarnessLab.**

```bash
HARNESSLAB_RESULTS=data/runs/prerecorded_mock HARNESSLAB_HARNESS=baseline python -m unittest discover -s trajectory_tests -v
HARNESSLAB_RESULTS=data/runs/mine python -m unittest discover -s trajectory_tests
```

On the mock data under `baseline`, expect `Ran 11 tests … FAILED (failures=13)`. The count is
higher than 11 because each test checks many runs, and some runs break the process rules. That
is the point of the exercise.

On Windows PowerShell, set the variables first:

```powershell
$env:HARNESSLAB_RESULTS="data/runs/prerecorded_mock"
$env:HARNESSLAB_HARNESS="baseline"
```

### 11.2 The infrastructure test suite (Python)

This checks HarnessLab itself: the runner, ledger, grader, analysis, backend routes and so on.

```bash
python -m unittest discover -s tests_agentlab -q        # or: make test   (macOS/Linux)
```

It takes one to three minutes and runs 1,145 tests. Expected result: **`OK`**, with some tests
reported as *skipped* (65 when this guide was tested).

| you see | why |
|---|---|
| `OK (skipped=…)` | everything passed. The skipped tests need material that is not in the zip (optional corpora, a git checkout, the macOS app) and are expected to be skipped. |
| `FAILED (failures=4)` when run as **root** (for example inside some containers) | four tests check that an unreadable file is reported; as root every file is readable, so they cannot pass. Run as a normal user. |

Anything else is worth reporting; see section 11.5.

### 11.3 The frontend test suite (optional, needs Node.js 20+)

```bash
cd harnesslab/frontend
npm ci                  # about 10 s
npx vitest run          # about 1.5 min
cd ../..
```

Expected: `Test Files 73 passed (73)` and `Tests 1400 passed (1400)`.

If you see *"act(...) is not supported in production builds of React"*, your shell has
`NODE_ENV=production` set. Run `NODE_ENV=test npx vitest run` instead.

To rebuild the interface after changing its source, run `npm run build` in the same folder, then
restart `python -m harnesslab`.

### 11.4 A five-minute smoke test

Run this after installing, or whenever you are unsure the install still works. The quickest way
is one command, which runs the checks below for you and says what to do if one fails:

```bash
python scripts/check_install.py      # or: make check  ->  All 5 checks passed
```

The same steps by hand:

```bash
python -m harnesslab --version                                   # harnesslab 0.3.0
python -m harnesslab run --provider mock --tasks all --repeats 3 --price 0,0 --out data/runs/smoke
#   -> 24 runs, 14 PASS
python exercises/ex1_variance.py --results data/runs/smoke       # prints four sections
python -m harnesslab --no-browser &                              # then open http://127.0.0.1:8765/#/rig/
```

In the browser, check four things:

1. `smoke` appears under Datasets, labelled *Mock control*.
2. Opening it shows a pass rate near 58 % (14 / 24).
3. **Trajectories** shows green ✓ and red × boxes.
4. Clicking a red box opens a run with a failure chip.

### 11.5 Reporting a problem

Send your instructor:

1. what you ran (the exact command, or the URL from the address bar; the part after `#` records
   the exact view)
2. what you expected, and what happened instead (paste the full terminal output)
3. `python --version`, your operating system, and the output of `python -m harnesslab --paths`
4. a screenshot, if it is a display problem

---

## 12. Other ways to run it

### 12.1 Docker (no local Python setup)

```bash
docker build -t harnesslab .
docker run -d --rm -p 127.0.0.1:8765:8765 --name harnesslab harnesslab
# open http://127.0.0.1:8765 ; stop with: docker stop harnesslab
```

The image uses Python 3.12 and is a few hundred MB. It includes whatever is in `data/runs/` when
you build it.

- New runs started from its web app use the bundled mock model unless you pass a key.
- Launch runs from the web app (Rig → Analysis → Run setup). `harnesslab run` typed inside the
  container cannot find the tasks.
- `docker compose up --build` also works, and it keeps new runs in `./data/runs` on your
  machine.

### 12.2 One offline HTML file

```bash
python -m harnesslab --export lab.html --export-results prerecorded_mock --export-runs 10
```

This writes a single self-contained file, about 6 MB for this example, that opens straight from
disk with no server. The Rig works in it, read-only.

- Anything that would launch or change runs is absent.
- Leave out `--export-results` to embed every dataset. The file gets much larger.
- `--export-runs` sets how many full run recordings are embedded (default 40).

### 12.3 The legacy console

HANDOUT.md mentions an older console with **Patterns**, **Queries** and **Attribution** pages
(`python -m harnesslab.core.serve`). It is **not included in this package**; the command starts but
serves no page. Exercise 3's script and the trajectory tests (section 11.1) cover the same ground.

---

## 13. Troubleshooting

| symptom | cause | fix |
|---|---|---|
| `ERROR: Package 'harnesslab' requires a different Python: 3.9.6 not in '>=3.10'` | you are on the system Python 3.9 (common on macOS) | install Python 3.12 and recreate the venv with `python3.12 -m venv .venv` (section 2) |
| `ModuleNotFoundError: No module named 'fastapi'` | the virtual environment is not active, or the install did not run | `source .venv/bin/activate` (Windows: `.venv\Scripts\Activate.ps1`), then `python -m pip install -e .` |
| `address already in use` / port 8765 busy | another HarnessLab (or another app) is running | stop it with Ctrl+C, or use `python -m harnesslab --port 8766` |
| the browser did not open | `--no-browser`, or no default browser | open <http://127.0.0.1:8765> yourself |
| blank page, or `"dist_built": false` in `--paths` | the built interface is missing (a partial or damaged unzip) | unzip the archive again, or run `cd harnesslab/frontend && npm ci && npm run build` |
| **every run grades `fail`, but the agent's own tests pass in the transcript** | the grader could not run Python in the task sandbox. Typical with **pyenv**, where a `python` shim exists but exits with an error. | Run the task's test command yourself in a fresh folder. Fix your pyenv global version or use a plain venv. Check a mock sweep gives 14 / 24 (section 8.3). **Never believe a 0 % or 100 % mock sweep.** |
| a mock sweep is all pass or all fail | same as above: the grader, not the model | as above |
| `RuntimeError: OPENROUTER_API_KEY is not set` | a real-model run without a key | `export OPENROUTER_API_KEY=...` in **the same terminal** (for the web app: the terminal that started the server) |
| HTTP 429 / 529 from the provider | rate limit or overload | the runner retries with back-off; lower `--repeats` or switch model |
| every run ends `exit=no_action` | the model does not call tools, or its replies are cut off at `max_tokens_per_call` | try a larger model, or raise `max_tokens_per_call` in a copied harness |
| costs look wrong for a mock or local model | no `--price` was given, so the default table was used | re-run with `--price 0,0` |
| the app shows no runs | it reads `data/runs/*` under the lab root | check `python -m harnesslab --paths`; start it from inside `harnesslab-school/` |
| "Invalid host" when opening from another machine | the server only accepts `127.0.0.1` / `localhost` by default | set `HARNESSLAB_ALLOWED_HOSTS=my-hostname` and start with `--host 0.0.0.0`. Only do this on a network you trust. |
| Buddy says *not connected* | no OpenRouter key saved in this browser | Settings → Buddy → paste the key → Save |
| Buddy's answer is cut off | the reply hit its output limit | ask a narrower question; the footer shows how many tokens were hidden reasoning |
| `python -m unittest discover` finds no tests | you are not in the package's top folder | `cd` into `harnesslab-school/`, the folder that contains `tests_agentlab/` |
| trajectory tests fail | that is usually the finding, not a bug | see section 11.1 |
| `request failed after 4 attempts: timed out` (run ends `exit=error`) | the model took more than 120 s per call | use a faster model (section 8.5) |
| `npm ci` adds only ~77 packages, then vitest says *Cannot find package '@vitejs/plugin-react'* | your shell has `NODE_ENV=production`, so npm skipped the development packages | `NODE_ENV=development npm ci`, then `NODE_ENV=test npx vitest run` |

### Windows

The runner copies each task into a scratch folder and runs its tests there. It was not tested on
native Windows for this guide.

If anything misbehaves, use **WSL2** (Ubuntu from the Microsoft Store; then follow the Linux
steps) or **Docker** (section 12.1). If you do use native Windows:

- use `py -3.12` instead of `python3.12`
- activate with `.venv\Scripts\Activate.ps1`
- set variables with `$env:NAME="value"`
- `make` is not available; use the `python -m ...` commands shown next to each `make` target

---

## 14. Safety, privacy and cost

- **The sandbox is not a security boundary.** An agent works on a *copy* of the task repository,
  and a command policy blocks network access, privilege escalation and secret access. Under the
  `permissive` harness, destructive commands really run inside that scratch copy. If you point a
  real model at it and care about your machine, run it in a container or VM.
- **The server listens only on your own machine** (`127.0.0.1`). It rejects requests from other
  websites and unknown host names. It is not meant to be exposed to a network without real
  authentication in front of it.
- **API keys:**
  - The key for command-line runs lives in your shell environment.
  - Buddy's key lives only in your browser.
  - Neither is written into results, exports or source.
  - Never commit a key, and use keys with a spending limit.
- **Cost:**
  - Mock runs, recorded data and local models are free.
  - A small, fast model such as `deepseek/deepseek-v4.1-flash` costs under $0.001 per run. A
    frontier model costs roughly $0.05–0.15 per run.
  - Set `--max-cost` on every real sweep.
  - The Rig's Run setup shows the estimated cost before anything starts.
- **Your runs stay local.** New folders under `data/runs/` are yours. Delete one to remove it.

---

## 15. Known issues

These were observed on 23 September 2026. None of them changes the recorded data.

1. **Pooled numbers under one model's name.** Some views and exercises summarise *all* models
   under a harness while a heading names one model. For example, `baseline` in `llma4se_live` has
   272 runs, which is all five models pooled. The Rig labels such figures **pooled** in amber;
   read the *n* before comparing.
2. **Strengthened grades for `real_swe_agent_500`.** These third-party runs carry no strengthened
   grade. Parts of the judge/integrity analysis report them as 0.0 % instead of "not graded". The
   Rig shows "not graded".
3. **In an exported HTML file**, the dataset picker in the "Asking…" sentence can list datasets
   that were not embedded. Only the embedded ones have data.
4. **HANDOUT.md and LAB_1H.md use older page names.** Use the mapping table in section 10.

---

## 16. Glossary

| term | meaning |
|---|---|
| **agent** | a language model that acts through tools (read, edit, run tests, submit) |
| **harness** | everything around the model: tools, policy, context rule, budgets, prompt, temperature |
| **cell** | one model under one harness |
| **task / probe** | one repository with an issue; a probe is a task with a built-in trap |
| **run / attempt / repeat** | one agent episode on one task; repeats are runs of the same cell and task |
| **ledger** | the per-run record of every consequential action (`ledger.jsonl`) |
| **span** | one line of the ledger |
| **oracle / test suite** | what decides pass or fail: visible, hidden (default) or strengthened |
| **boundary event** | an action the policy flagged: destructive, network, secret, test tampering… |
| **mock** | a scripted fake agent with designed failure rates, for offline, reproducible work |
| **sentinel** | an early-warning model that scores a run *while it is happening* and can block a bad submit |
| **pass@k / pass^k** | at least one of *k* passes / all *k* pass |
| **flip** | a task whose outcome changes across repeats of the same cell |
| **paired Δ** | a difference computed task by task between two cells |
| **cut off** | a reply that stopped at the per-call output limit (`finish_reason: length`) |

---

## 17. Where to go next

- **[HANDOUT.md](../HANDOUT.md)**: the two-hour lab, with the question for each exercise.
- **[README.md](../README.md)**: what the zip contains, running it in Docker, the local server's
  security checks, and the included datasets.
- **[docs/student-lab.md](student-lab.md)**: how Student Lab · Guided Analysis works.
- In the Rig, press **Ctrl K** and type *reading guide*.
