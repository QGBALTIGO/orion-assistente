# Publicação Railway da central humana

## Escopo e autorização

O pedido de 20/10/2026 autoriza publicar na mesma branch. Permanecem proibidos
os testes com mensagens reais, desconexão de contas e alteração de registros
comerciais. A atualização de esquema é aditiva; nenhum seed é executado.
Mídias completas, executor de grafos, exportações e webhooks avançados continuam
com as limitações registradas na matriz; publicação não equivale a homologação.

## Alvo conferido

- Conta QG BALTIGO; projeto `strong-creation` (`55750961-99ca-495e-b55e-4c07d78e53df`).
- Ambiente `production` (`162d0407-3d4f-4163-9cb2-0c90154c707f`).
- API `orion-assistente`: `1d39a32e-8297-4e5b-8b55-d6540542e80b`.
- Worker `orion-worker`: `f6c51234-ee8b-49ba-a06e-bcad37b54b67`.
- QR `orion-qr-bridge`: `ef72fd3d-fcef-4be6-8aa2-a1a544b021f7`.
- PostgreSQL 16, Redis e volumes existentes preservados.
- Nenhuma alteração pendente foi encontrada no ambiente antes deste trabalho.
- Os três serviços estavam fixados em `24456e82155609bcb8cde2c72d4aac3612a91b54`.

## Migração revisada

`python -m scripts.one_time_inbox_migration` aceita somente a cadeia 0023 -> 0028
com os cinco arquivos conferidos por SHA256 antes de qualquer I/O. Reaproveita
o protocolo existente de backup criptografado e armazenamento privado no volume
do bridge. Exige leitura de volta, restauração em banco isolado sem acesso dos
papéis da aplicação, comparação de hashes de todas as linhas antigas, catálogo,
privilégios e programas, e ensaio das mesmas migrações. O banco temporário é
removido somente após conferir nome, OID e proprietário.

A transação no banco original exige que o snapshot ainda coincida com o backup,
usa o papel app_migrator e compara novamente os registros e a estrutura antiga.
Somente são permitidas as novas tabelas/funções, o evento de cancelamento e as
colunas de versão dos rascunhos. Pausas Meta ativas são importadas como humanas,
sem retomar IA por expiração. Qualquer falha impede promover o novo processo.

O worker e a API usam `python -m scripts.wait_for_inbox_schema`, que consulta
somente metadados como app_runtime; não recebe credenciais administrativas.
`start_api` remove a autorização e a identificação da migração antes do Uvicorn.

Variáveis específicas da operação: `ORION_RUN_INBOX_MIGRATION=1` e
`ORION_INBOX_MIGRATION_RUN_ID=<UUID v4 novo>`. Reutilizar as credenciais e a
identidade do banco já configuradas; não copiá-las para logs ou documentação.
O pre-deploy antigo de onboarding não deve ser executado sobre o esquema 0028.

## Reversão

Preservar os deployment IDs anteriores e seus comandos de pre-deploy. Se uma
barreira falhar antes da promoção, manter a versão anterior atendendo. Depois
da migração aditiva é possível voltar ao código anterior com flags da central
desativadas, sem downgrade destrutivo nem restauração sobre dados novos.
Quando houver pausas humanas sob a autoridade central, manter essa autoridade:
não desligar INBOX_CONTROL_ENABLED como forma de pausar transporte.

## Recibo

Preparação em andamento. Resultados de CI, backup, migração, deployments e
verificações HTTP serão registrados após a execução, sem antecipar sucesso.
