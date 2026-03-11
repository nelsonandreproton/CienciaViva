FROM python:3.12-slim

# Create non-root user
RUN useradd --create-home --shell /sbin/nologin monitor

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY monitor.py .

# Data volume for persistent state
RUN mkdir -p /data && chown monitor:monitor /data

USER monitor

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

ENTRYPOINT ["python", "monitor.py"]
