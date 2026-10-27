#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "First run: setting things up, this takes a minute..."
  python3 -m venv .venv && . .venv/bin/activate && python -m pip install -r requirements.txt
else
  . .venv/bin/activate
fi
python moodle_sync.py "$@"
