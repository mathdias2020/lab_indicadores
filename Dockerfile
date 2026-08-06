FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY src /app/src
COPY manifests /app/manifests
COPY requirements-worker.txt /app/requirements-worker.txt

RUN pip install --no-cache-dir -r /app/requirements-worker.txt

# scp may preserve private directory modes from the host checkout. Normalize
# image readability before dropping to the non-root worker UID.
RUN chmod -R a+rX /app/src /app/manifests

# The VPS labadmin account uses UID 1000. Numeric identity keeps bind-mounted
# run, log and work files owned by the laboratory account.
USER 1000:1000

ENTRYPOINT ["python", "/app/src/lab_indicadores/worker.py"]
