# syntax=docker/dockerfile:1

# =========================================================================
# VPV viewer (vpv-view) container — the web server that serves the React
# viewer, streams clips, and composes side-by-side videos. This is the piece
# meant to be deployed (e.g. to Railway); the `vpv` verifier is a local
# browser-automation CLI and is not run here.
# =========================================================================

# --- Stage 1: build the React viewer SPA -----------------------------------
FROM node:20-slim AS web
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm ci
COPY frontend/ ./frontend/
# vite's outDir is ../src/vpv/web (relative to frontend/), so this writes the
# built SPA to /app/src/vpv/web, which the Python stage picks up below.
RUN cd frontend && npm run build

# --- Stage 2: Python runtime ------------------------------------------------
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    VPV_VIEW_DIR=/data
WORKDIR /app

# Install the package. Copy metadata + source, drop in the freshly built SPA
# (overwriting any committed copy so the image always matches source), then
# install. imageio-ffmpeg bundles a static ffmpeg, so no apt packages needed.
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY --from=web /app/src/vpv/web ./src/vpv/web
RUN pip install .

# Clip storage. Mount a Railway volume here to persist uploads/renders across
# deploys; without one, /data is ephemeral (fine for a stateless demo).
RUN mkdir -p /data

# Railway injects $PORT; vpv-view reads it (and HOST/VPV_VIEW_DIR) from env.
EXPOSE 8000
CMD ["vpv-view"]
