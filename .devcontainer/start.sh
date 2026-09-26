#!/usr/bin/env bash
# Start the HarnessLab workbench in the background of a dev container / codespace, once.
# postStartCommand runs on every start; a second start while it is up does nothing.
set -u
cd "$(dirname "$0")/.."
PORT="${HARNESSLAB_PORT:-8765}"
LOG=/tmp/harnesslab.log

up() { curl -fsS -o /dev/null "http://127.0.0.1:${PORT}/api/overview" 2>/dev/null; }

if up; then
  echo "HarnessLab is already running on port ${PORT}."
  exit 0
fi

# setsid + nohup: the server must outlive the postStartCommand shell that launched it.
setsid nohup python -m harnesslab --no-browser --port "${PORT}" >"${LOG}" 2>&1 < /dev/null &

for _ in $(seq 1 90); do
  if up; then
    echo "HarnessLab is running on port ${PORT} (log: ${LOG})."
    grep -m1 "Codespaces" "${LOG}" || true
    exit 0
  fi
  sleep 1
done
echo "HarnessLab did not answer on port ${PORT} within 90 s. Last lines of ${LOG}:" >&2
tail -n 20 "${LOG}" >&2
exit 1
