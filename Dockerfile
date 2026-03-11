FROM python:3.12-slim

# gosu: standard Docker pattern for dropping privileges after fixing volume ownership
RUN apt-get update && \
    apt-get install -y --no-install-recommends gosu && \
    rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /sbin/nologin monitor

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY monitor.py entrypoint.sh ./
RUN chmod +x entrypoint.sh

# /data is created here so Docker knows it exists; ownership is fixed at runtime by entrypoint.sh
RUN mkdir -p /data

HEALTHCHECK --interval=25h --timeout=15s --retries=2 \
  CMD python -c "\
import json, sys; \
from datetime import datetime, timezone, timedelta; \
from pathlib import Path; \
f = Path('/data/state.json'); \
sys.exit(1) if not f.exists() else None; \
s = json.loads(f.read_text()); \
last = datetime.fromisoformat(s['last_check'].replace('Z','+00:00')); \
sys.exit(0 if datetime.now(timezone.utc) - last < timedelta(hours=26) else 1)"

ENTRYPOINT ["/app/entrypoint.sh"]
