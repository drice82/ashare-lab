FROM python:3.12-slim AS backend
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 POLARS_MAX_THREADS=2
ENV RUFF_CACHE_DIR=/tmp/ruff-cache MYPY_CACHE_DIR=/tmp/mypy-cache PYTEST_ADDOPTS="-p no:cacheprovider"
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml alembic.ini ./
COPY backend ./backend
COPY scripts ./scripts
RUN pip install --no-deps . && useradd --uid 1000 --create-home lab && mkdir -p /app/data && chown lab:lab /app/data
USER lab
ENV DATA_DIR=/app/data DATABASE_PATH=/app/data/business.db
CMD ["ashare", "serve"]

FROM node:22-bookworm-slim AS web-build
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM nginx:stable-alpine AS frontend
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=web-build /web/dist /usr/share/nginx/html
EXPOSE 80
