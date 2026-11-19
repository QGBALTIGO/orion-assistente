# WhatsApp: confirmações de transporte e recuperação autorizada

## Correção de 19 de novembro de 2026

A revisão `42007a009d2fde8582b4c59ee0a9e0a5da02664a` corrige duas omissões
de ACK do Baileys 6.7.24 e a captura de textos próprios nos canais gerenciados.
Ela parte da revisão exata do QR Bridge em produção,
`23e2f40bef9b9594586d64f4d2736212025ade51`. A API, o worker e os esquemas do
banco não fazem parte dessa publicação.

Imagem verificada:

```text
ghcr.io/qgbaltigo/orion-assistente-qr-bridge@sha256:e374f21ad9d3bd8e0c68c13a60f7c73a7334439833561e0c447165b08dce6d45
```

[Execução de testes e publicação](https://github.com/QGBALTIGO/orion-assistente/actions/runs/44554652403).

O workflow histórico `publish-migration-images.yml`, presente na branch de
migração, fixa revisões anteriores. Reexecutá-lo não produz esta correção.
Usar o digest verificado acima como referência de recuperação e revisar
explicitamente a revisão de origem em qualquer publicação futura. A branch
deste reparo serve para construir o QR Bridge; não é uma nova base de release
da API ou do worker.

## Por que o ACK importa

O transporte do WhatsApp exige uma confirmação para determinadas stanzas.
Omissões podem deixar entregas pendentes no servidor, provocar encerramento do
stream e reapresentar mensagens. Uma reapresentação pode encontrar um estado
Signal que já avançou. Por isso, um erro criptográfico observado depois de
uma reconexão não identifica, sozinho, a causa da reconexão.

Os relatos upstream relevantes são a
[ausência de ACK de mensagens](https://github.com/WhiskeySockets/Baileys/issues/1910),
a [ausência de ACK de status](https://github.com/WhiskeySockets/Baileys/issues/2858)
e o [tratamento de status online e offline](https://github.com/WhiskeySockets/Baileys/pull/2883).
Esses relatos descrevem ciclos próximos de 50 minutos. A atribuição de um
incidente específico deve considerar também as evidências do próprio canal.

### Comportamento do backport

- Mensagens recebem ACK depois de descriptografia, limpeza e `upsertMessage`
  bem-sucedidos no SDK.
- Uma mensagem com falha criptográfica mantém o fluxo de retry/NACK e não
  recebe confirmação positiva apenas para interromper sua reapresentação.
- Caminhos que já enviavam ACK, como mensagens ignoradas ou indisponíveis,
  mantêm seu comportamento sem um segundo ACK acrescentado pelo patch.
- Stanzas `status` válidas recebem ACK tanto online quanto na fila offline.
  Falha ao enviar esse ACK não bloqueia a próxima tarefa da fila.
- O ACK confirma recebimento no transporte. Ele não envia recibo de leitura,
  não implementa leitura de conteúdo de status e não prova conclusão de um
  agendamento ou persistência de negócio. O SDK possui buffer de eventos.

## Instalação e atualização da dependência

`qr-bridge/scripts/patch-baileys-acks.mjs` aceita exclusivamente a versão
6.7.24 e os hashes conhecidos de `lib/Socket/messages-recv.js`:

| Artefato | SHA-256 |
| --- | --- |
| Original | `f949a18ee1dfef50a00a767bc2fd5e696dc7b2c1af386a31b4ac7fbb167b6519` |
| Corrigido | `874f303048ad7de6a064c973c3a714a2cae5ce3444381209095fae3649bd43a8` |

A instalação recusa versão, conteúdo ou patch parcial inesperados. A escrita
é atômica, seguida de verificação de leitura; reaplicar é idempotente.

```sh
cd qr-bridge
npm ci --omit=dev --ignore-scripts --no-audit --no-fund
node scripts/patch-baileys-acks.mjs
node scripts/patch-baileys-acks.mjs --check
```

O Dockerfile executa o patch explicitamente. O `postinstall` cobre instalações
normais com scripts habilitados. `--check` verifica sem alterar arquivos e
falha quando o patch está ausente.

Ao atualizar Baileys, revisar os handlers reais da nova versão e os caminhos
online/offline. Só retirar o backport depois de verificar que ambos os ACKs
estão presentes e de adaptar/executar os testes de regressão. Não relaxar a
verificação de hash para fazer uma versão diferente passar pelo instalador.

## Cache de retry e origem dos textos

O canal gerenciado captura ecos próprios recentes em eventos `notify` e
`append`, conforme a política existente de texto privado simples. Exige
identidade própria, destinatário privado válido, conteúdo elegível, timestamp
de até 15 minutos e posse atual da sessão. Mídia, conteúdo efêmero, histórico
antigo e mensagens sem identidade válida não são importados por esse caminho.

O conteúdo permanece no armazenamento cifrado existente. A origem
`linked_self` é protegida junto ao conteúdo; mensagens criadas pelo bridge
usam `bridge_generated`. Isso preserva a distinção entre comandos manuais do
proprietário e mensagens geradas pelo sistema, inclusive após reinício.
Um eco não sobrescreve nem prolonga um registro existente.

A captura não concede autorização para retransmitir. `readAuthorizedRetry`
continua validando identificador, destinatário, participante, direção e
sessão. Uma relação PN/LID diferente exige comprovação pelo socket atual.
Não existe fallback que retorne uma mensagem apenas por seu identificador.

## Como interpretar os novos registros

Os eventos gerenciados incluem `channel_id`. Os identificadores de mensagem
são transformados em HMAC antes de sair do processo. Correlacionar por canal,
deployment e `trace`; não publicar conteúdo, telefones ou material de sessão.

| Evento | O que comprova | O que não comprova |
| --- | --- | --- |
| `protocol_ack_sent` | O envio do ACK de transporte concluiu; `kind` distingue mensagem e status | Entrega a terceiro, leitura ou conclusão de negócio |
| `stream_ack_missing` | O provedor apontou um ACK faltante; classe/tipo são sanitizados | Perda definitiva ou necessidade de apagar a sessão |
| `get_message`, `cached=true` | O cache forneceu conteúdo após autorização | Que a retransmissão chegou ao destinatário |
| `get_message`, `cached=false` | O mecanismo recusou ou não encontrou conteúdo; `reason` qualifica o motivo | Que a mensagem nunca existiu ou foi perdida definitivamente |
| `retry_capture`, `category=captured` | Um eco próprio elegível foi persistido para retry | Que todos os tipos de mensagem são recuperáveis |
| `receipt_update` | O SDK recebeu uma atualização de recibo para mensagem própria | Que um cache hit isolado tenha sido entregue |

`retry_capture` com `ineligible_content` é uma decisão da política de captura;
não deve ser contado como falha de `get_message`. Um mesmo identificador pode
aparecer novamente porque o provedor retransmitiu uma stanza.

### Motivos de recusa ou ausência

- `absent`: não há registro recuperável; não afirma sua causa histórica.
- `expired` / `capacity_evicted`: prazo ou capacidade removeram o registro.
  Essas causas usam marcas limitadas em memória; depois de reiniciar, um
  registro removido pode aparecer honestamente como `absent`.
- `decryption_failed`, `corrupt_record`, `unsupported_format`, `storage_error`
  e classificações de arquivo: problema de leitura/integridade do cache.
- `peer_mismatch`, `participant_mismatch`, `identity_id_mismatch`,
  `unbound_identity`, `stored_direction` e classificações de identidade:
  a identidade solicitada não satisfaz a autorização.
- `alias_proof_*`: comprovação de relação PN/LID indisponível, negada ou falhou.
- `stale_session` / `authorization_failed`: a posse/autorização atual não
  permitiu a leitura.
- `hit` / `hit_verified_alias`: conteúdo disponível e autorizado.

## Verificação operacional

1. Confirmar o digest da imagem, o deployment `SUCCESS` e `build_commit`
   emitido pelo processo. Um deploy por imagem não depende de SHA de Git
   fornecido pela plataforma: a revisão é incorporada durante o build.
2. Confirmar a retomada do mesmo canal e da mesma geração de sessão. A troca
   de proprietário/fence durante uma publicação é esperada. Preservar o
   volume e manter uma única réplica proprietária do socket.
3. Verificar `connected`, socket aberto, buffer liberado e filas de entrada.
   Uma amostra sem identificação do canal não deve ser atribuída a ele.
4. Verificar ACKs reais e eventuais retries/recibos. Testes automatizados não
   enviam mensagens para contas reais. Uma validação com destinatário real
   precisa de autorização própria para o envio.
5. Observar pelo menos um intervalo completo do padrão de recorrência,
   contado da nova conexão. Registrar o começo e o fim da janela; não
   apresentar uma janela curta como prova de estabilidade por 50 minutos.
6. Se a falha reaparecer, correlacionar `disconnect_evidence`, classe do ACK,
   `trace`, `decrypt_failed` e motivos de `get_message`. Não confirmar uma
   mensagem indecifrável como recebida com sucesso para esconder o erro.

Um transporte legado revogado pode emitir `needs_attention`/401 enquanto um
canal gerenciado permanece conectado. Esses estados precisam ser atribuídos
ao transporte correto. Reinicialização, novo vínculo, exclusão de credenciais
e reenvio manual de histórico não são procedimentos automáticos deste reparo.

## Evidência de testes do release

A publicação executou **1.394 testes de runtime com sucesso, sem falhas** em
Node 22, incluindo os handlers reais compilados do SDK em um harness offline,
fila offline, retry, isolamento de canais, persistência cifrada e comandos
manuais. A imagem construída também passou pela verificação do patch.

A suite completa inclui `panel-ui.test.mjs`, que lê arquivos estáticos da
aplicação Python fora da imagem QR Bridge. Suas 19 falhas foram reproduzidas
na revisão-base, com os mesmos nomes e arquivos inalterados. Elas não foram
silenciadas nos testes gerais; o gate desta imagem executa o runtime que ela
contém. Essa exclusão não deve ser reutilizada como evidência de validação do
painel ou de uma publicação da API.
