# Decifer Trading — Production Runtime Image
#
# Purpose: packages the Decifer 4.0+ runtime for future cloud deployment.
#          This image is NOT currently deployed. It exists for validation and
#          readiness preparation only.
#
# Key decisions:
#   - Runs as non-root user `decifer` (UID 1000) for security.
#   - Multi-stage build: build tools excluded from final image.
#   - TA-Lib 0.4.0 built from source — reliable across cloud base images.
#   - NLTK vader_lexicon downloaded at build time → no internet needed at runtime.
#   - No secrets baked in; all keys injected via --env-file or environment.
#   - archive/, tests/, data/, logs/ excluded via .dockerignore.
#   - Default CMD: lightweight healthcheck. NOT the live bot.
#
# Static analysis findings (2026-05-11, cloud/cloud-readiness-validation-hardening):
#   - Docker daemon unavailable on development machine; build not yet validated live.
#   - TA-Lib source build path confirmed correct for multi-stage layout.
#   - NLTK vader_lexicon was missing from image — added in this branch.
#   - Non-root user (UID 1000) pattern confirmed safe.
#   - TA-Lib headers removed from runtime stage (build-time only artefact).
#
# Build (validation only — does not start the bot):
#   docker build -t decifer-trading:4.0 .
#
# Lightweight health check (safe — no broker, no orders):
#   docker run --rm decifer-trading:4.0
#
# Full preflight check (requires env + data mount):
#   docker run --rm --env-file .env \
#     -v "$(pwd)/data:/app/data" \
#     decifer-trading:4.0 \
#     python3 scripts/cloud_preflight.py
#
# The live bot (bot.py) requires IBKR Gateway accessible and all env vars set.
# Never change the default CMD to bot.py without explicit Amit approval.

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1: builder — system deps + TA-Lib source build + Python packages
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        wget \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Build TA-Lib 0.4.0 C shared library from source.
# Pinned to 0.4.0 — the version the TA-Lib Python wrapper (>=0.4.28) targets.
# Installing to /usr/local so the shared library is found by the Python wheel.
RUN wget -q https://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz \
    && tar -xzf ta-lib-0.4.0-src.tar.gz \
    && cd ta-lib \
    && ./configure --prefix=/usr/local \
    && make -j"$(nproc)" \
    && make install \
    && cd .. \
    && rm -rf ta-lib ta-lib-0.4.0-src.tar.gz

WORKDIR /build
COPY requirements.txt .
# Install all Python packages into /install prefix for clean stage copy
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: runtime — lean image, no compiler, no wget
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy TA-Lib shared library from builder (headers excluded — not needed at runtime)
COPY --from=builder /usr/local/lib /usr/local/lib

# Refresh dynamic linker cache so libta_lib.so.0 is found at import time
RUN ldconfig

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# ── NLTK vader_lexicon ────────────────────────────────────────────────────────
# Download the VADER lexicon at image build time so no internet access is
# required when the container runs. Data written to /usr/share/nltk_data
# (system-wide, readable by the non-root user).
#
# social_sentiment.py has a graceful fallback if VADER is unavailable, but
# baking it into the image is cleaner and more reliable than a runtime download.
#
# NLTK_DATA is set as a persistent environment variable so every process
# (bot.py, social_sentiment.py, healthcheck.py) finds the lexicon automatically.
ENV NLTK_DATA=/usr/share/nltk_data
RUN python3 -m nltk.downloader -d /usr/share/nltk_data vader_lexicon \
    && chmod -R a+r /usr/share/nltk_data

# ── Non-root user ─────────────────────────────────────────────────────────────
# Running as root inside a container is a security anti-pattern.
# Host bind-mount directories (./data, ./logs) must be writable by UID 1000.
# On Linux: chown -R 1000:1000 ./data ./logs before first docker-compose up.
RUN groupadd --gid 1000 decifer \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash decifer

WORKDIR /app

# Copy application source (archive/, tests/, data/, logs/ excluded by .dockerignore)
COPY . .

# Belt-and-suspenders: remove any .env that slipped past .dockerignore
RUN rm -f .env .env.local .env.*.local

# Create runtime directories; chown everything to the non-root user
# (NLTK_DATA is at /usr/share/nltk_data — separate from /app, no chown needed)
RUN mkdir -p data/live data/heartbeats data/intelligence data/universe_builder \
             data/reference data/runtime logs \
    && chown -R decifer:decifer /app

USER decifer

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

# ── Default: lightweight health check ────────────────────────────────────────
# Confirms the runtime is intact (imports, dirs, env var presence) without
# touching the broker, placing orders, or requiring IBKR connectivity.
# Override CMD for specific workers; never hard-code bot.py here.
CMD ["python3", "scripts/healthcheck.py"]
