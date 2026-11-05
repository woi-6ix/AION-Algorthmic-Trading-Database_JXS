#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
echo 'Open http://127.0.0.1:8000 in your browser'
.venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8000
