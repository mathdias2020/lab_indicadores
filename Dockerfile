FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY src /app/src
COPY manifests /app/manifests

# The VPS labadmin account uses UID 1000. Numeric identity keeps bind-mounted
# run, log and work files owned by the laboratory account.
USER 1000:1000

ENTRYPOINT ["python", "-m", "lab_indicadores.worker"]
