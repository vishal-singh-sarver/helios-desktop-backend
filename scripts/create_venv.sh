#!/usr/bin/env bash
# Create venv and install dev dependencies
set -eu
cd "$(dirname "$0")/.."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements-dev.txt
echo "Done. Activate with: source venv/bin/activate"
