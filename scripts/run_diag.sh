#!/usr/bin/env bash
cd "$HOME/scholar-trust" || exit 1
export PYTHONPATH="$PWD" PYTHONIOENCODING=utf-8
.venv/bin/python scripts/diag_py401.py
