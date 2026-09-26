# HarnessLab workshop: start in a browser

The practical part of the lab runs in **GitHub Codespaces**: a Linux machine in the cloud with
Python 3.12 and HarnessLab already installed. There is nothing to install on your laptop, and every
seat in the room runs the same environment. You need a browser and a GitHub account.

## Three steps

1. **Sign in to GitHub and open <https://codespaces.new/aura-lab-wm/harnesslab-workshop>.** The **Open in
   GitHub Codespaces** button at the top of the README goes to the same page.
2. **Paste your own OpenRouter key into `OPENROUTER_API_KEY`** (see *Your key* below), or leave it
   empty to work offline for now. Then click **Create codespace**.
3. **Wait about a minute.** The workbench opens in a new browser tab. If it doesn't, open the
   **Ports** tab at the bottom of the editor and click the globe next to *HarnessLab workbench*
   (port 8765).

That's it. The editor tab behind it holds the repository: `exercises/`, `tasks/`, `harnesses/`
and a terminal.

## What works without a key

Everything in the method, on the recorded datasets: the questions already answered, the analysis
steps, every run and span, the judge and integrity checks, the sentinel, the canvas. The mock
agent also runs offline from the terminal:

```bash
python -m harnesslab run --provider mock --tasks t01_slugify --repeats 3 --out data/runs/mine
python exercises/ex1_variance.py --results data/runs/mine
```

New results appear under **Datasets** in the workbench.

## Your key

The workshop provides the platform. Live model calls go on **your own OpenRouter account**.

1. Create an account at <https://openrouter.ai> and add a few dollars of credit. A small model
   costs well under a cent per run. The whole lab on a frontier model costs a few dollars.
2. Create a key at <https://openrouter.ai/keys>. Give it a **credit limit** (for example $5), so
   a runaway experiment cannot spend more than that.
3. Paste it in step 2 above. GitHub keeps it as *your* Codespaces secret. It is never written to the
   repository, and nobody else in the room can see it. Change or delete it any time under
   github.com → Settings → Codespaces → Secrets.

## Live runs

Launch runs from **Analysis → Run setup** in the workbench, or from the terminal:

```bash
python -m harnesslab run --provider openrouter --model deepseek/deepseek-v4-flash \
  --tasks t01_slugify,t03_ratelimit --repeats 3 --out data/runs/live-mine
```

If you skipped the key in step 2, paste it under **Settings → The lab's key**. The server holds it in
memory until the codespace stops. For a key that survives restarts, add it as a Codespaces secret
named `OPENROUTER_API_KEY` (github.com → Settings → Codespaces), then restart the codespace.

## Good to know

- **Privacy.** The workbench URL (`…-8765.app.github.dev`) is private to your GitHub account.
  Nobody else can open it unless you change the port's visibility.
- **Stopping.** A codespace stops by itself after 30 minutes idle. Reopen it from
  github.com/codespaces and the workbench starts again. Your runs under `data/runs/` are kept.
- **Free allowance.** A personal GitHub account includes 60 hours a month on the default 2-core
  machine, far more than the workshop uses. Delete the codespace afterwards from
  github.com/codespaces.
- **If the workbench is not there,** run `bash .devcontainer/start.sh` in the terminal. It starts
  the server, or tells you it is already running. The server log is `/tmp/harnesslab.log`.

## Other ways in

- **Your own laptop:** `docs/STUDENT_GUIDE.md` §1 (Python 3.10 or newer, `pip install -e .`).
- **Docker:** `SCHOOL_PACKAGE.md`, "Run in a container".
- **Any Dev Containers host** (VS Code's Dev Containers extension, the `devcontainer` CLI): this
  repository's `.devcontainer/` works the same way as in Codespaces.

## For the instructor

- **Keys.** Students bring their own OpenRouter key (above). The platform never needs one to show the
  recorded datasets.
- **The public repository.** Students open <https://github.com/aura-lab-wm/harnesslab-workshop>,
  a public copy of the school package: the same allow-list, so no instructor solutions, captured
  sessions, keys or Git history. Anyone with a GitHub account can open it without being invited,
  and each codespace counts against the student's own free monthly allowance, not yours.
- **Updating it.** The development repository stays private. After changing anything students
  should see, commit it there and run `python3 scripts/publish_workshop_repo.py` (add `--dry-run`
  first to see the diff). It rebuilds the package and pushes one commit that names its source.
- **Hidden tests are readable.** `tasks/*/hidden_tests/` ship with the package, so a student who
  looks can read them. They are hidden from the *agent* (never copied into its workspace), which
  is what the measurement needs.
