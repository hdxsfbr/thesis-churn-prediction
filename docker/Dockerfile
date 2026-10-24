FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY config ./config
COPY sql ./sql

RUN pip install --no-cache-dir ".[snowflake]" \
    && useradd --create-home --uid 1000 churn \
    && chown -R churn:churn /app

ENV CHURN_CONFIG_DIR=/app/config
USER churn
EXPOSE 8000
CMD ["churn", "serve", "--host", "0.0.0.0", "--port", "8000"]
