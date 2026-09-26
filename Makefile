# harnesslab — the boring commands, in one place.
#
#   make test            the Python suite
#   make test-js         the JavaScript suite (vitest, node environment, no browser, no network)
#   make menubar-test    the Swift suite for the capture menu-bar app (macOS, needs swift)
#   make test-all        both suites, python first
#   make build-frontend  build the React UI into harnesslab/frontend/dist
#   make wheel           build a wheel into dist/ and verify it installs in a clean venv
#   make school-package  build the school replication package and verify it in a clean room
#   make docker          build the container image
#
# Everything is phony: this is a task runner, not a build system.

PY      ?= python3
VENV    ?= /tmp/harnesslab-venv
WHEELDIR?= dist
DISTOUT ?= harnesslab/frontend/dist

.PHONY: help test test-v test-js menubar-test test-all build-frontend lint wheel wheel-check school-package docker docker-run bench index clean

help:
	@grep -E "^[a-z][a-z-]*:.*##" $(MAKEFILE_LIST) | sed -E "s/^([a-z-]+):.*## /  \\1|/" | awk -F"|" '{printf "  %-16s %s\n", $$1, $$2}'

test:  ## run the unittest suite
	cd $(CURDIR) && $(PY) -m unittest discover -s tests_agentlab -q

test-v:  ## run the unittest suite, verbose
	cd $(CURDIR) && $(PY) -m unittest discover -s tests_agentlab -v

test-js:  ## run the JavaScript suite (vitest, node environment, no browser, no network)
	cd harnesslab/frontend && npm ci --no-audit --no-fund && npx vitest run

menubar-test:  ## run the Swift suite for the capture menu-bar app (macOS only, needs swift)
	cd macos/CaptureMenubar && swift test

test-all: test test-js  ## both suites, python first
	@echo "both suites ran"

build-frontend:  ## npm ci + vite build into harnesslab/frontend/dist
	cd harnesslab/frontend && npm ci --no-audit --no-fund && npx vite build --outDir $(CURDIR)/$(DISTOUT)

lint:  ## oxlint over the frontend sources
	cd harnesslab/frontend && npx oxlint

wheel: build-frontend  ## build the wheel (UI included) into dist/
	$(PY) -m pip wheel . -w $(WHEELDIR) --no-deps
	@ls -l $(WHEELDIR)/*.whl

wheel-check: wheel  ## install the wheel in a throwaway venv and smoke-test the CLI
	rm -rf $(VENV)
	$(PY) -m venv $(VENV)
	$(VENV)/bin/pip -q install $(WHEELDIR)/harnesslab-*.whl
	$(VENV)/bin/harnesslab --version
	$(VENV)/bin/harnesslab --help >/dev/null
	HARNESSLAB_LAB=$${HARNESSLAB_LAB:-$$(mktemp -d)} $(VENV)/bin/harnesslab --paths

school-package:  ## build the school replication package and verify it in a clean room
	# Uses the already-committed harnesslab/frontend/dist as-is (see build-frontend's own doc): a
	# fresh `npx vite build` here can content-hash the same source to different filenames across
	# Node/Rollup versions, which then makes the archive fail its own no-silent-drop check against
	# what git actually tracks. Run `make build-frontend` yourself first if dist/ needs updating,
	# and restore it (`git checkout -- $(DISTOUT) && git clean -fdq $(DISTOUT)`) before committing.
	$(PY) scripts/build_school_package.py --verify

docker:  ## build the container image (tag follows the pyproject.toml version)
	docker build -t harnesslab:$$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml) .

docker-run:  ## run the image on http://127.0.0.1:8765 (bound to 127.0.0.1 only)
	docker run --rm -p 127.0.0.1:8765:8765 -e OPENROUTER_API_KEY="$$OPENROUTER_API_KEY" \
		harnesslab:$$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)

index:  ## rebuild the SQLite run index from scratch
	$(PY) -m harnesslab.backend.store --rebuild

bench:  ## time the SQLite index against load_index()
	$(PY) -m harnesslab.backend.store

clean:  ## remove build artifacts and the derived run index
	rm -rf build $(WHEELDIR) *.egg-info $(VENV)
	rm -f data/.harnesslab_index.sqlite data/.harnesslab_index.sqlite-wal data/.harnesslab_index.sqlite-shm \
	      data/.holdstill_index.sqlite data/.holdstill_index.sqlite-wal data/.holdstill_index.sqlite-shm
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
