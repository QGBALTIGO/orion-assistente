FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/app
# PostgreSQL 16 matches the production server. These clients are used only by
# the explicit one-time migration script; the normal startup remains unchanged.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl openssh-client \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl --fail --silent --show-error --proto '=https' --tlsv1.2 \
       --http1.1 --retry 5 --retry-all-errors --retry-delay 2 \
       --connect-timeout 15 --max-time 90 \
       https://www.postgresql.org/media/keys/ACCC4CF8.asc \
       -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo 'deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main' > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-16 ffmpeg \
    && ffmpeg -version >/dev/null && ffprobe -version >/dev/null \
    && pg_dump --version | grep -E '^pg_dump \(PostgreSQL\) 16\.' \
    && pg_restore --version | grep -E '^pg_restore \(PostgreSQL\) 16\.' \
    && rm -rf /var/lib/apt/lists/*
# Fixed upstream release and digest, independent of a mutable image tag.
# The production Railway service uses Linux amd64.
RUN curl --fail --silent --show-error --proto '=https' --tlsv1.2 \
       --http1.1 --retry 5 --retry-all-errors --retry-delay 2 \
       --connect-timeout 15 --max-time 90 \
       https://github.com/jpillora/chisel/releases/download/v1.12.0/chisel_1.12.0_linux_amd64.gz \
       -o /tmp/orion-chisel.gz \
    && echo 'f3f180f1d93aa72cce4e6386f98cc06569a0146fbd65eb4423cf83e6434bcfe6  /tmp/orion-chisel.gz' | sha256sum -c - \
    && gzip -dc /tmp/orion-chisel.gz > /usr/local/bin/chisel \
    && chmod 0755 /usr/local/bin/chisel \
    && /usr/local/bin/chisel --version | grep -F '1.12.0' \
    && rm /tmp/orion-chisel.gz
COPY pyproject.toml requirements.lock ./
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY tests/ ./tests/
COPY scripts/ ./scripts/
COPY landing/ ./landing/
RUN pip install --no-cache-dir -c requirements.lock ".[test]" \
    && python -m pip check \
    && useradd --uid 10001 --create-home appuser

USER appuser
EXPOSE 8000
CMD ["python", "-m", "scripts.start_api"]
