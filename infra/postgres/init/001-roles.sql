-- Executar como administrador PostgreSQL SOMENTE na inicialização de ambiente novo.
-- Variáveis passadas por psql; valores de senha não aparecem neste arquivo.
\set ON_ERROR_STOP on
SELECT format(
  'CREATE ROLE app_runtime LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS',
  :'runtime_password'
) \gexec
SELECT format(
  'CREATE ROLE app_migrator LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS',
  :'migrator_password'
) \gexec
REVOKE ALL ON DATABASE :"db_name" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db_name" TO app_runtime, app_migrator;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO app_runtime, app_migrator;
GRANT CREATE ON SCHEMA public TO app_migrator;
-- Tabelas futuras têm grants conservadores; migration define os grants de cada tabela.
ALTER DEFAULT PRIVILEGES FOR ROLE app_migrator IN SCHEMA public
  REVOKE ALL ON TABLES FROM PUBLIC;
