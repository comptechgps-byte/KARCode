FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ ./
# telemetry.py / diagnosis.py / remediation.py / safety.py are imported as
# flat modules by main.py, so we keep them at the same directory level.

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python3", "main.py"]
