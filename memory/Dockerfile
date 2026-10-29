# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY gateway ./gateway
COPY schemas ./schemas
COPY migration ./migration
RUN --mount=type=cache,target=/root/.cache/pip pip install .
EXPOSE 8377
CMD ["python", "-m", "gateway.app.main"]
