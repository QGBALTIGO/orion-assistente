-- Executar UMA vez em banco criado nas FATIAS 0/1 ou quando necessário.
-- CREATE ON DATABASE permite app_migrator criar a extensão confiável btree_gist.
-- Não concede nenhum acesso extra à API/runtime/suporte.
\set ON_ERROR_STOP on
GRANT CREATE ON DATABASE :"db_name" TO app_migrator;
