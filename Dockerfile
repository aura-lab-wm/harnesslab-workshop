# harnesslab — the platform, in a container.
#
#   docker build -t harnesslab .
#   docker run --rm -p 127.0.0.1:8765:8765 -e OPENROUTER_API_KEY=sk-or-... harnesslab
#   -> http://127.0.0.1:8765
#
# Stage 1 builds the React UI (node is not needed at runtime); stage 2 is a plain python:3.12-slim
# with the wheel installed. The lab root inside the image is /lab, so harnesses/, tasks/ and
# data/runs/ resolve through harnesslab.backend.paths' "checkout" branch with no configuration.
#
# The image carries only the four school datasets (see harnesslab/school-package.json
# "datasets"): the build context is trimmed by .dockerignore's data/runs allow-list, never by a
# broad `COPY . .` here — this Dockerfile also has to build correctly from an *extracted* school
# package, which ships without .dockerignore (the builder skips dotfiles) and may already have a
# .venv sitting next to it.

# --------------------------------------------------------------------------- 1. build the UI
FROM node:22-slim AS ui
# Mirror the checkout's layout (harnesslab/frontend/...) so the UI's relative import of
# ../../school-package.json (src/App.jsx, src/PackageContents.jsx -> harnesslab/school-package.json)
# resolves inside the build, exactly as it does in a checkout.
WORKDIR /ui/harnesslab/frontend
COPY harnesslab/frontend/package.json harnesslab/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY harnesslab/frontend/ ./
COPY harnesslab/school-package.json /ui/harnesslab/school-package.json
RUN npm run build

# --------------------------------------------------------------------------- 2. runtime
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HARNESSLAB_LAB=/lab \
    HARNESSLAB_NO_BROWSER=1

WORKDIR /lab

# Package metadata first: setup.py computes `packages`/`package_dir` from what's on disk (see its
# docstring), so pip needs it alongside pyproject.toml or the install silently ships incomplete.
COPY pyproject.toml setup.py README.md ./
COPY harnesslab/ ./harnesslab/
COPY harnesses/ ./harnesses/
COPY tasks/ ./tasks/
COPY exercises/ ./exercises/
COPY trajectory_tests/ ./trajectory_tests/
COPY tests_agentlab/ ./tests_agentlab/
COPY data/ ./data/
COPY --from=ui /ui/harnesslab/frontend/dist/ ./harnesslab/frontend/dist/

# Never bake in whoever built the image's local sentinel selection (harnesslab/data/active_model.txt
# is package-relative state, not lab-root data, so .dockerignore can't touch it): every image boots
# on the bundled mock model, same as scripts/build_school_package.py does for the zip handoff.
RUN printf 'mock_prerecorded\n' > harnesslab/data/active_model.txt

# harnesslab/docs/ deliberately never reaches either build path: harnesslab/school-package.json
# excludes it from the zip handoff, and .dockerignore's "harnesslab/docs" line excludes it from
# THIS build's context (a checkout build never gets that far via COPY harnesslab/ ./harnesslab/).
# early_warning_metric.md cites aggregate numbers from the optional real_swe_agent_500 dataset,
# which SCHOOL_PACKAGE.md tells students is not included, so the derived-stats page has to go too,
# in the image exactly as in the zip. setup.py's DATA_PACKAGES already filters itself to
# directories that exist on the checkout (see setup.py's `_existing()`), so the missing directory
# no longer fails `pip install .`; this mkdir just stands in an empty one so package_data's *.md
# glob has somewhere to match nothing.
RUN mkdir -p harnesslab/docs

RUN pip install --no-cache-dir . && python -c "import harnesslab.backend.app"

# The agent sandbox is a scratch copy plus a command policy, not a security boundary: run unprivileged.
RUN useradd --create-home --uid 10001 harnesslab && chown -R harnesslab:harnesslab /lab
USER harnesslab

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/results', timeout=2).status==200 else 1)"

CMD ["harnesslab", "--host", "0.0.0.0", "--port", "8765", "--no-browser"]
