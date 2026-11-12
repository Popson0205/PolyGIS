# One service: the React web app and the FastAPI API in one image (Railway builds this from the repo root).
# The API is served under /api and the web app at /.

# ---- 1. build the web app ----
FROM node:20-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ENV VITE_API_URL=/api
RUN npm run build

# ---- 2. the API, serving the built web app ----
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# libexpat1: rasterio's GDAL (bundled in its wheel) reads XML with the system's expat, which slim images leave out
RUN apt-get update && apt-get install -y --no-install-recommends curl libexpat1 && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY backend/requirements.txt .
RUN pip install -r requirements.txt \
 && python -c "import rasterio, pyogrio, shapely, pyproj, skimage, contourpy, scipy, networkx, sklearn; print('GIS libraries load: GDAL', rasterio.__gdal_version__)"
COPY backend/app ./app
COPY backend/migrations ./migrations
COPY --from=web /web/dist ./web
ENV API_PREFIX=/api WEB_DIR=/srv/web ENV=prod
RUN useradd -m gis && chown -R gis /srv
USER gis
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD curl -fs http://localhost:${PORT:-8000}/api/health || exit 1
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
