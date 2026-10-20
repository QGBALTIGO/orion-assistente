FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/app
# PostgreSQL 16 matches the production server. These clients are used only by
# the explicit one-time migration script; the normal startup remains unchanged.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl --fail --silent --show-error --proto '=https' --tlsv1.2 \
       https://www.postgresql.org/media/keys/ACCC4CF8.asc \
       -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo 'deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main' > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-16 \
    && pg_dump --version | grep -E '^pg_dump \(PostgreSQL\) 16\.' \
    && pg_restore --version | grep -E '^pg_restore \(PostgreSQL\) 16\.' \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml requirements.lock ./
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY tests/ ./tests/
COPY scripts/ ./scripts/
COPY landing/ ./landing/
RUN pip install --no-cache-dir -c requirements.lock ".[test]" \
    && useradd --uid 10001 --create-home appuser

USER appuser
EXPOSE 8000
CMD ["python", "-m", "scripts.start_api"]
