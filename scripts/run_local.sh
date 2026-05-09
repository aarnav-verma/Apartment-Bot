#!/usr/bin/env bash
set -e
python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
python -m src.poll --env .env "$@"
