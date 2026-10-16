-- Provisionamento idempotente de app_support em VOLUME POSTGRES JÁ EXISTENTE.
-- Rodar somente como administrador do cluster com senha vinda do ambiente privado.
\set ON_ERROR_STOP on
SELECT format(
  'CREATE ROLE app_support LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS',
  :'support_password'
) WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_support') \gexec
SELECT format('ALTER ROLE app_support PASSWORD %L', :'support_password') \gexec
GRANT CONNECT ON DATABASE :"db_name" TO app_support;
GRANT USAGE ON SCHEMA public TO app_support;
