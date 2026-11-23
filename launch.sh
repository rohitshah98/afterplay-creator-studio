#!/bin/zsh
set -eu
cd "${0:A:h}"
export PATH="/opt/homebrew/opt/ffmpeg-full/bin:/opt/homebrew/bin:$PATH"
if [[ -f .env ]]; then set -a; source .env; set +a; fi
if [[ -z "${DATABASE_URL:-}" ]]; then unset DATABASE_URL; fi
if [[ ! -d frontend/dist ]]; then npm --prefix frontend run build; fi
exec .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8787 --no-access-log
