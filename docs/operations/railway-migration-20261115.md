# Conferência da migração Railway — 15/11/2026

Destino: projeto `Orion`, conta Gabrieli, ambiente `production`.
Origem: projeto `strong-creation`, conta Orion, ambiente `production`.

## Resultado observado

- Os cinco serviços de produção do destino estão online, com uma réplica em execução por serviço e sem alterações pendentes.
- O worker final foi publicado com `SUCCESS`: `d02e29fd-fd08-4498-ab0b-6784c380e291`.
- A configuração original de pré-deploy do worker foi restaurada e relida. As referências temporárias de diagnóstico foram esvaziadas.
- API, QR bridge, Redis e PostgreSQL da origem continuam offline. A inspeção isolada dos buckets executada no antigo serviço worker terminou; sua réplica está em zero processos, sem executar ARQ, e seu comando de manutenção foi restaurado.

## Arquivos: comparação direta nas duas contas

Os buckets foram lidos em cada conta com suas próprias referências nativas, sem copiar credenciais para a conversa. O verificador emitiu apenas hashes dos nomes dos objetos, tamanho, SHA-256 do conteúdo e metadados técnicos.

| Grupo | Origem | Destino | Bytes no destino | Ausentes | Conteúdo divergente |
| --- | ---: | ---: | ---: | ---: | ---: |
| Mídias | 79 | 79 | 2.121.425 | 0 | 0 |
| Backups | 48 | 48 | 39.602.660 | 0 | 0 |

A conferência também comprovou que os valores de bucket e endpoint usados pela API correspondem aos buckets nativos do projeto de destino. Os registros da migração anterior confirmam restauração de 3.417 arquivos de sessão com SHA-256 verificado; essa contagem não substitui uma conexão WhatsApp válida.

## Banco de destino

O diagnóstico consultou as 159 tabelas em transação `REPEATABLE READ, READ ONLY`, com timeout de consulta/lock e papel `app_migrator`. Nenhuma tabela ficou inacessível; nenhuma constraint estava pendente de validação. A revisão é `0079_service_archive`.

Amostra das contagens: 337 clientes, 163 agendamentos, 14 profissionais, 40 serviços, 12 empresas e 16 contas de painel. São contagens atuais do destino, não uma comparação de todos os registros com o banco congelado da origem.

Os papéis `app_runtime`, `app_support` e `app_migrator` não são superusuários, não ignoram RLS e não podem criar papéis. Apenas as tabelas globais `alembic_version` e `plans` não usam RLS.

O PostgreSQL original permanece desligado. A igualdade integral dos registros de origem/destino ainda não está comprovada; a presença das tabelas e a comparação dos arquivos de backup não constituem essa prova.

## WhatsApp: depende de novo vínculo

A conexão principal devolve `pair_failed`, `disconnectCode=401`, `reason=logged_out`, `action=reauthenticate` e `messagesEnabled=false`. O socket revogado está preservado e o armazenamento não informa indisponibilidade. O canal adicional mantém amostras de conexão saudável.

É necessária nova confirmação do vínculo pelo responsável no WhatsApp. Reinicialização de infraestrutura não substitui essa autenticação. Não houve envio de mensagens de teste nem exclusão de credenciais preservadas.

`/health/whatsapp` confirma somente que a ponte responde. Seu HTTP 200 não prova conexão de todas as sessões nem entrega de mensagens.

## Execução e testes

A inspeção completa de banco/bridge executou no pré-deploy `74819293-47bd-4d34-a115-be59693afe6b`. A parte de armazenamento dessa tentativa encontrou as antigas variáveis temporárias de migração vazias. A conferência foi então feita com referências nativas em cada projeto:

- Destino: `e5258ab6-0043-4b57-80f2-ff73016a290f`, diagnóstico de arquivos concluído e worker publicado com `SUCCESS`.
- Origem: `dc125403-99b8-414a-8b9e-720ce943b3d8`, inspeção somente leitura concluída e processo encerrado.
- Limpeza do destino: `d02e29fd-fd08-4498-ab0b-6784c380e291`, `SUCCESS`.

As primeiras republicações reutilizaram a configuração anterior. A aplicação por `connect-service-source` com a mesma imagem imutável fez a configuração corrigida entrar em vigor. Nenhuma mudança funcional da aplicação foi publicada por esta PR.

Onze testes locais cobrem detecção de perda/corrupção, estilo S3 da Railway, remoção de informações sensíveis, comparação de manifestos e fechamento de streams:

```sh
python -m unittest discover -s tests/unit -p test_verify_railway_migration.py -v
```

A migração não foi certificada como 100% concluída: vínculo do WhatsApp e comparação integral dos registros do banco continuam pendentes.
