# Decifer Trading — Cloud Runtime Container
#
# Purpose: cloud preflight verification and intelligence worker execution.
#          Does NOT start live trading by default.
#
# TA-Lib requires the C shared library installed in the base image.
# Uses python:3.11-slim + ta-lib build from source (minimal install).
#
# Build:
#   docker build -t decifer-trading .
#
# Preflight check (safe — no broker, no orders):
#   docker run --rm --env-file .env decifer-trading python3 scripts/cloud_preflight.py
#
# Universe committed worker (safe — data only):
#   docker run --rm --env-file .env -v $(pwd)/data:/app/data decifer-trading \
#     python3 universe_committed.py --run-once
#
# The bot itself (bot.py) is NOT the default CMD.
# Never add live order submission capability to this image without Amit approval.

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# ── System deps for TA-Lib C library ─────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Build TA-Lib C library from source (required before pip install TA-Lib).
# Version pinned to match requirements.txt TA-Lib>=0.4.28.
RUN wget -q https://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz \
    && tar -xzf ta-lib-0.4.0-src.tar.gz \
    && cd ta-lib \
    && ./configure --prefix=/usr \
    && make -j"$(nproc)" \
    && make install \
    && cd .. \
    && rm -rf ta-lib ta-lib-0.4.0-src.tar.gz

# ── Python dependencies ───────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application source ────────────────────────────────────────────────────────
# Secrets are never baked into the image. All keys must come from --env-file or
# environment variables at runtime.
COPY . .

# Remove any local .env file that may have been copied (safety guard).
RUN rm -f .env .env.local

# Ensure runtime dirs exist inside the container (writable via volume mount).
RUN mkdir -p data/live data/heartbeats data/runtime data/intelligence data/reference logs

# ── Default: preflight check ──────────────────────────────────────────────────
# Override CMD to run workers or other safe processes.
# Never override CMD to start bot.py without explicit Amit approval.
CMD ["python3", "scripts/cloud_preflight.py"]
