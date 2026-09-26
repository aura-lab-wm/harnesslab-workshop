# HarnessLab — school replication package

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/aura-lab-wm/harnesslab-workshop)

This is the teaching handoff for the coding-agent evaluation lab.

**Workshop: open it in GitHub Codespaces** (the button above). Nothing to install; bring your own
OpenRouter key, or none for the recorded datasets. See [`docs/WORKSHOP.md`](docs/WORKSHOP.md).

**Start with [`docs/STUDENT_GUIDE.md`](docs/STUDENT_GUIDE.md).** It covers installing, a first
offline run, the web interface, running experiments, reading the results, the exercises, testing
and troubleshooting, step by step. `HANDOUT.md` holds the lab exercises and their questions;
`LAB_1H.md` is the one-hour teaching plan.

## Run the dashboard

Python 3.10 or newer is required (3.12 recommended). On macOS the built-in `python3` is 3.9 and
will not install this package: install 3.12 from python.org or Homebrew first and use
`python3.12` below. From the extracted `harnesslab-school` directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
python3 -m harnesslab --no-browser
```

Open **http://127.0.0.1:8765**. No API key is needed to inspect the included results.
The built frontend is included; Node.js is only needed if you change its source.

The app (the keyboard-first **Rig workbench**) opens on **Datasets**: every results directory
under `data/runs/`, which in this package means the included datasets below plus any runs you
make yourself. Teaching material (a reading guide and a lesson beside each attempt) is an
opt-in preference under **Settings → Student Lab · Guided Analysis**.

The archive physically omits excluded data; send the archive, not the whole checkout.

## Run in a container

An alternative to the venv install above, from the extracted `harnesslab-school`
directory (or the full checkout) — Docker only, no local Python setup needed:

```bash
docker build -t harnesslab:school .
docker run -d --rm -p 127.0.0.1:8765:8765 --name harnesslab harnesslab:school
```

Open **http://127.0.0.1:8765**. The image ships only the datasets listed below, and
new runs started from it use the bundled mock model (so they need no key), regardless of any local model selection
on the machine that built it. `docker build` works the same way from a git
checkout or from this extracted archive, even with a `.venv` already sitting
next to it.

Or with Compose:

```bash
docker compose up --build
```

Compose also bind-mounts `./data/runs` into the container, so new runs land
next to the ones already on disk and survive `docker compose down` — in a
developer checkout (not this extracted package) that also means the container
can see whatever else is already in `data/runs/` on that machine. Set
`OPENROUTER_API_KEY` in the environment (or a `.env` file next to
`docker-compose.yml`) for live cells against real models; without it the mock
provider and pre-recorded runs still work.

Both binds are `127.0.0.1`-only by default. For access from another machine on
the LAN, set `HARNESSLAB_ALLOWED_HOSTS` to the extra Host header(s) the backend
should accept (defaults: `127.0.0.1`, `localhost`, `[::1]`).

## Local server security

The local server binds to `127.0.0.1` by default and now enforces two checks in front of
every request:

- **Host allow-list.** A request whose `Host` header is not `127.0.0.1`, `localhost`, `::1`,
  the interface you passed to `--host` (when it isn't `0.0.0.0`/`::`), or a host listed in
  `HARNESSLAB_ALLOWED_HOSTS` (comma-separated) is rejected before it reaches any route. This
  is what stops **DNS rebinding** -- a domain an attacker controls, re-resolved to point at
  this machine, riding a real browser tab into the app.
- **Same-origin writes.** Any POST/PUT/PATCH/DELETE carrying an `Origin` or `Sec-Fetch-Site`
  header that doesn't say "this page" is rejected, and a write's `Content-Type` must be
  `application/json`. This stops a hostile page open in another tab from firing a write at the
  server while you have it running -- the class of attack usually called CSRF.

Running behind a reverse proxy or in a container reached under another hostname? Set
`HARNESSLAB_ALLOWED_HOSTS=my-host.example` (comma-separated for more than one).

**This is defense against a hostile web page, not a security boundary against another user
or process on the same machine.** Anyone who can reach `127.0.0.1` on this port at all --
another local user, another process, a port-forward -- has the same access the browser UI
does. Treat the sandbox this way: fine for a classroom laptop, not something to expose past
`127.0.0.1`/your own LAN without adding real authentication in front of it.

## Included datasets

| Dataset | Runs | Source |
| --- | ---: | --- |
| `llma4se_live` | 1,632 | **Real.** Five models (DeepSeek V4 Flash, Gemini 2.5 Flash-Lite, Gemini 3.8 Flash, GPT-5.6 Luna, Qwen3.8 27B) × six harnesses × 8 tasks, collected for this lab through OpenRouter. Default for exercises 1–6 and 8. |
| `live` | 576 | **Real.** Claude Haiku 4.5, Gemini 2.5 Flash, GPT-5-mini × eight harnesses × 8 tasks, collected for this lab through OpenRouter. Second input to exercise 8. |
| `real_swe_agent_500` | 500 | **Real, third-party.** SWE-agent (Llama 8B / 70B) on 30 GitHub issues, from [nebius/SWE-agent-trajectories](https://huggingface.co/datasets/nebius/SWE-agent-trajectories) (CC-BY-4.0). Used by `ex7 --offline`. |
| `prerecorded_mock` | 480 | Mock model × six harnesses × 8 tasks — for runs that must be identical on every machine. |
| `prerecorded_mock_weak` | 160 | Weaker mock model — the 2 × 2 mock version of exercise 8. |
| `families_mock` | 80 | Two mock models × four harnesses × 5 tasks. |
| `demo_mock` | 24 | A compact mock demonstration. |
| `captured_claude_code` | 1 | **Real, captured.** One Claude Code session (Claude Sonnet 5, 28 steps) implementing a task in this repository: `results_scope.py` and its test, nine test runs, tested before it stopped. Recorded by the capture spine, machine paths replaced with `/workspace/harnesslab`. A session nobody launched as an experiment: no oracle, one repeat, a harness you did not choose. |

2,709 of the 3,453 included runs are real model runs. They are replayed from disk: reading and
reanalysing them needs no API key. Only running *new* experiments against live models does.

## Reproduce the exercises

From this directory, after installation:

```bash
python3 exercises/ex1_variance.py
python3 exercises/ex2_harness.py
python3 exercises/ex7_real_trajectories.py --offline
python3 exercises/ex8_experiment.py
```

Read `HANDOUT.md` for exercises 3–6 and interpretation. Figures are optional;
install `matplotlib` if needed. To generate a small, new offline run:

```bash
python3 -m harnesslab run --provider mock --tasks t01_slugify --repeats 3 --out data/runs/mine
```

New results appear on the dashboard next to the included datasets. They do not
become part of the school package; to ship one, add it to the manifest and rebuild.

## Contents and exclusions

The package includes the application source and built UI, exercises, task
repositories, harness configurations, trajectory checks, infrastructure tests,
attribution, and the datasets above. The mock sentinel model is selected in
the archive independently of the developer's local model selection.

It excludes every other results directory, the captured-session store (only the one
curated session above ships), credentials, environment files,
caches, local model selection, personal outputs, Git history, the macOS menu-bar
app, machine census tools, internal notes, and instructor solutions. Reusable
capture modules remain application dependencies; no watcher state or
configuration is included. Some integration tests target optional corpora or
machine tooling outside this teaching package; they require the full checkout.

`PACKAGE_MANIFEST.json` lists each shipped file's relative path, size, and SHA-256
checksum. Its own checksum is not included recursively. It describes the actual
archive; `harnesslab/school-package.json` defines the selection policy.

## Rebuild the handoff (maintainers)

`harnesslab/frontend/dist` is committed, so most rebuilds need nothing more than:

```bash
python3 scripts/build_school_package.py --verify
```

`make school-package` does the same thing. `--verify` extracts the fresh archive into a clean
room, installs it in a throwaway venv with no other harnesslab on the path, starts the server and
drives it (the four core exercises, a live mock run, the shipped test suite, a re-hash of every
file against `PACKAGE_MANIFEST.json`, and a scan for machine paths or real-shaped secrets), and
prints a JSON verdict. Treat a non-zero exit as a broken package, not something to ship anyway.

Only rebuild the frontend itself after changing its source:

```bash
cd harnesslab/frontend
npm ci
npm run build
cd ../..
git add harnesslab/frontend/dist   # commit the new build -- see the next paragraph for why
python3 scripts/build_school_package.py --verify
```

The builder ships `harnesslab/frontend/dist` exactly as git tracks it; it never rebuilds it
itself. A local `npm run build` you have NOT committed yet will leave the working tree holding
different (content-hashed) filenames than the ones `git ls-files` still reports -- and the
builder's no-silent-drop check will then correctly refuse to ship an archive whose dist/ has
drifted from what the repository says is there. Commit the rebuilt dist/ (or restore it with
`git checkout -- harnesslab/frontend/dist && git clean -fdq harnesslab/frontend/dist` if you were
only checking something locally) before packaging.

Send **`dist/harnesslab-school.zip`**. The builder uses the same manifest as the
dashboard, rejects symlinks in selected inputs, skips hidden files and caches,
ships every tracked file under an included root regardless of its extension, fails the build
if a tracked file under an included root would otherwise be silently dropped, and does not
include any unlisted results directory. It generates the package README and selects the bundled
mock model without changing local configuration.
