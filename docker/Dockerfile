FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl unzip git \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    "dbt-duckdb==1.11.0" \
    "duckdb==1.5.6" \
    "pandas==2.3.3" \
    "numpy==2.5.4" \
    "pyarrow==19.0.1" \
    "PyYAML==6.0.3" \
    "python-docx==1.2.0" \
    "matplotlib==3.11.2"

WORKDIR /workspace
