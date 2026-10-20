> Estado mais recente: a versão anterior foi publicada às 16:45 UTC em
> `a7199de`; a central permaneceu desligada. A nova transição foi qualificada
> e sua publicação de 17:36 UTC foi cancelada no controle de aprovação da
> integração. Consulte [o recibo atual](legacy-transition.md#recibo-final-desta-tentativa--20102026).
> As informações abaixo descrevem a tentativa anterior e seus limites naquele momento.

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

Preservar os deployment IDs anteriores e seus comandos de pre-deploy. Ao voltar
ao código anterior com esquema 0028, desativar ORION_RUN_ONBOARDING_MIGRATION:
o migrador anterior aceita somente 0019/0023 e deve ser ignorado no rollback. Se uma
barreira falhar antes da promoção, manter a versão anterior atendendo. Depois
da migração aditiva é possível voltar ao código anterior com flags da central
desativadas, sem downgrade destrutivo nem restauração sobre dados novos.
Quando houver pausas humanas sob a autoridade central, manter essa autoridade:
não desligar INBOX_CONTROL_ENABLED como forma de pausar transporte.

## Recibo

### Código e testes — IMPLEMENTADO E TESTADO

Commit qualificado: `b04e8de7060619fc9b2637cf0161530a1a20635c`.
Mesma branch `codex/orion-marcflow-integrations-20261020`, PR #80.

- [CI PostgreSQL, Redis, Docker, QR e interface](https://github.com/QGBALTIGO/orion-assistente/actions/runs/39456118412): sucesso.
- Backend: **4.202 passed, 1 skipped**, 135 avisos, 206,61 s. Inclui operação
  completa 0023 -> 0028 com pg_dump/pg_restore real e dados sintéticos, preservação
  de pausas, reversão transacional por snapshot divergente e barreira app_runtime.
- Compose: **4.087 passed, 116 skipped**, 10 avisos, 102,60 s. Conjuntos sobrepostos;
  não somar ao resultado acima. Testes do runner de migração que exigem owner e
  clientes dedicados ficam no job PostgreSQL, sem ampliar o papel runtime.
- QR: **416 passed, 0 failed**; DOM: **306 passed, 0 failed**. Build e smoke da
  biblioteca real sem envio externo aprovados; áudio/ASR também aprovado.
- [CI de mídias](https://github.com/QGBALTIGO/orion-assistente/actions/runs/39456118399): sucesso.
- Local: 84 testes do procedimento, configuração, barreira e startup passaram.
- Navegador: seis combinações 360/768/1440 px, claro/escuro; central e rascunhos,
  usando somente APIs sintéticas. Nenhuma mensagem real enviada.

### Railway — BLOQUEADO NA CONFIRMAÇÃO, NÃO PUBLICADO

Em 20/10/2026, após revisão do patch, a chamada `accept_deploy` retornou:
**“Cancelled — the user did not approve this action. No changes were made.”**
Não foi contornado o controle de aprovação. Nenhum build/deployment novo foi
iniciado por essa chamada; não existe recibo de backup/migração em produção.
O teste de restauração acima ocorreu somente no CI descartável.

Patch preparado: `44be2f12-6c0e-40d7-b335-154e96b3ec13`, **27 alterações**,
não destrutivas, apenas nos três serviços Orion. Banco, Redis, bucket, domínios,
volumes e credenciais não têm alterações preparadas. A confirmação ainda é
necessária para aplicar esse patch. Não usar outro canal para contornar a recusa.

Planejado no patch (ainda NÃO vigente):

- Mesma branch, SHA qualificado fixado nos três serviços.
- API: pre-deploy revisado com conferência das versões testadas, migração
  protegida, barreira runtime e probe somente de leitura API -> bridge.
- Worker: barreira runtime 0028 antes de iniciar o novo processamento.
- INBOX_TOOLS_ENABLED=1; INBOX_CONTROL_ENABLED=0 e INBOX_SEND_ENABLED=0 na API e no
  worker. Conferir o piloto global existente antes de adotar a nova autoridade:
  ativá-la bloqueia o piloto legado por projeto e pode interromper a IA atual.
- Operação de migração: UUID `ebd63173-7d71-4b4b-b7ce-28d43112bb60`.
- Nenhuma credencial Meta/QR nova e nenhum opt-in de campanhas ou envio real.

Deployments anteriores, conferidos ainda vigentes após o cancelamento:

| Serviço | Deployment |
|---|---|
| API | `f2f1f525-c9b2-482e-b178-6c432e13664f` |
| Worker | `ee2b2416-b886-4487-92d1-01e6de6cec92` |
| QR | `b37aca1e-fc08-4c3d-a5ce-0e6b8740765f` |

Os cinco serviços do projeto continuam online, sem falha/alerta na consulta.
Antes da tentativa, `/health` e `/health/whatsapp` responderam 200 e o painel 200.
Isso comprova a disponibilidade da versão anterior, não da nova central.
Depois de uma aprovação efetiva, aguardar a operação terminar, conferir recibo
criptografado/restauração e revisão 0028, comparar hashes dos assets publicados,
validar saúde dos três serviços e o isolamento HTTP sem mensagens reais.
