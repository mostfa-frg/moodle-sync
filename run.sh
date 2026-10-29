#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1

PY=python3
command -v python3 >/dev/null 2>&1 || PY=python
VPY=".venv/bin/python"

# A broken environment (e.g. its Python was removed) is rebuilt.
if [ -e .venv ] && ! "$VPY" -c "pass" >/dev/null 2>&1; then
  echo "The saved environment is broken. Rebuilding it..."
  rm -rf .venv
fi

if [ ! -x "$VPY" ]; then
  echo "First run: setting things up, this takes a minute..."
  "$PY" -m venv .venv || { echo "Could not create the environment. Is Python 3 installed?"; exit 1; }
fi

# Make sure the packages are really inside it.
if ! "$VPY" -c "import playwright" >/dev/null 2>&1; then
  echo "Installing the required packages..."
  "$VPY" -m pip install -r requirements.txt || { echo "Setup did not finish. Try: pip install playwright"; exit 1; }
fi

"$VPY" moodle_sync.py --no-pause "$@"
