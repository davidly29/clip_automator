# syntax=docker/dockerfile:1

# =========================================================================
# VPV control panel container — one image that runs BOTH the web control
# panel (vpv-view: auth, clip library, side-by-side compose) AND the
# verifier (vpv/Playwright/Chromium) so checks can be launched from the UI.
# Meant to be deployed (e.g. to Railway). Set VPV_ADMIN_PASSWORD to require
# login; without it the panel runs open (not recommended for public deploys).
# =========================================================================

# --- Stage 1: build the React control-panel SPA ----------------------------
FROM node:20-slim AS web
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm ci
COPY frontend/ ./frontend/
# vite's outDir is ../src/vpv/web (relative to frontend/), so this writes the
# built SPA to /app/src/vpv/web, which the Python stage picks up below.
RUN cd frontend && npm run build

# --- Stage 2: Python + Chromium runtime ------------------------------------
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    VPV_VIEW_DIR=/data \
    VPV_IN_CONTAINER=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app

# Install the package. Copy metadata + source, drop in the freshly built SPA
# (overwriting any committed copy so the image always matches source), then
# install. imageio-ffmpeg bundles a static ffmpeg, so no apt ffmpeg needed.
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY --from=web /app/src/vpv/web ./src/vpv/web
RUN pip install .

# Install Chromium + its OS dependencies for the in-container verifier.
# Runs headless with software-GL flags (see vpv.jobs.CONTAINER_BROWSER_ARGS).
RUN python -m playwright install --with-deps chromium

# Clip storage. Mount a Railway volume here to persist uploads/renders/runs
# across deploys; without one, /data is ephemeral (fine for a stateless demo).
RUN mkdir -p /data

# Railway injects $PORT; vpv-view reads it (and HOST/VPV_VIEW_DIR) from env.
# Admin credentials are NOT baked in — set them at deploy time.
EXPOSE 8000
CMD ["vpv-view"]
