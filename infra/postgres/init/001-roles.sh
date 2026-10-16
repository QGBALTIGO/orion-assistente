#!/bin/sh
set -eu
# Executado uma única vez no initdb inicial da imagem oficial PostgreSQL.
# Senhas vêm apenas de variáveis privadas do ambiente/container.
: "${POSTGRES_DB:?}"
: "${POSTGRES_USER:?}"
: "${APP_RUNTIME_PASSWORD:?}"
: "${APP_MIGRATOR_PASSWORD:?}"

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 \
  --set=db_name="$POSTGRES_DB" \
  --set=runtime_password="$APP_RUNTIME_PASSWORD" \
  --set=migrator_password="$APP_MIGRATOR_PASSWORD" \
  --file=/etc/orion/001-roles.sql
