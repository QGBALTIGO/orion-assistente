# Etapa 06 — contrato de Diagnósticos

Estado: implementado para validação em CI, sem envio real na qualificação.

Base pública: `/api/v1/tenants/{tenant_id}/subscription-whatsapp/diagnostics`.
Permissão: `diagnostics.manage`, proprietário atual, empresa ativa e nicho
Serviço de assinaturas. A autorização é repetida antes de admitir uma ação e
antes do transporte. Credenciais e configuração privada não chegam ao navegador.

## Leituras e relatório

| Método e sufixo | Resultado |
| --- | --- |
| `GET /` | `{devices:[{id,name,version}],selected_device_id:null,generated_at,timezone}`. SQL somente; nenhum probe sem seleção. |
| `GET /{device_id}` | As cinco seções abaixo. Status privado e health podem ser consultados, com limites de 6 s e 5 s. Nenhum comando ou gravação. |
| `GET /requests/{idempotency_key}` | Recupera a intenção/resultado autorizado. Nenhum reenvio, probe, reconciliação ou escrita SQL. A rota é registrada antes da rota genérica. |

Todos os GETs retornam `Cache-Control: no-store`. Não chamam `_refresh`,
`device_event`, `/effects/pending`, compilação de regras ou reconciliação.
Leituras de status podem encerrar um contexto revogado na ponte para manter a
autorização; nunca iniciam sessão ou mensagem. O health é do processo da ponte,
independente da sessão WhatsApp. Sua latência mede API → ponte, não o telefone.

O relatório tem `device:{id,name,version}`, `generated_at`, `timezone` e:

- `connection:{state,label,source,observed_at,freshness,phone_e164}`. `source`
  é `bridge|stored`, `freshness` é `current|stale|unknown`. Estados conhecidos
  são os do módulo Dispositivos, mais `unknown`. Uma leitura indisponível nunca
  promove o cache a estado atual.
- `server:{state,latency_ms,observed_at,measurement:"api_to_bridge_health"}`.
  `state` é `available|unavailable|unknown`; latência é nula sem uma medição válida.
- `recent_messages:{limit:3,items:[...]}`. A decisão Orion de mostrar três linhas
  usa tentativas do dispositivo selecionado, por `created_at DESC,id DESC`.
  Cada linha tem `id,created_at,recipient,sent,type,status,status_label,outcome,truncated`.
  `recipient` e `sent` são as projeções seguras de Mensagens Enviadas; não contêm
  URL de mídia privada, resultado bruto, tokens ou conteúdo recebido. Não há ação
  de reenvio. `sent` significa aceitação do transporte, nunca entrega/leitura.
- `proxy:{mode,host,port,configuration_version,applied_to_session,can_test,blocked_reason,last_test}`.
  `mode` é `direct|configured|unknown`; aplicação é `yes|no|unknown`. Host/porta
  vêm apenas da configuração autorizada. Nunca são expostos usuário, senha ou URL
  completa. `last_test` usa a projeção de resultado proxy, mais `request_id` e
  `freshness`. Só é `current` com a mesma configuração e observação há no máximo
  90 s; o horário continua visível. Um resultado antigo permanece histórico.
- `test_message:{can_prepare,default_recipient_e164,text,requires_confirmation:true,blocked_reason}`.
  O número padrão exige PN próprio verificado pela sessão atual. Não usa cadastro
  da empresa nem `test_phone` como fallback.

Versões (`version`, `configuration_version`, `confirmation_version`) são SHA256
opacos de 64 caracteres hexadecimais minúsculos. Não são credenciais.

## Mensagem: preparação durável e confirmação

Texto fixo do servidor:

> Mensagem de teste do Orion. Envio solicitado pelo proprietário para verificar a conexão do WhatsApp.

1. `POST /{device_id}/test-messages/prepare` (201), corpo
   `{idempotency_key,expected_version,phone:null|string}`. Uma intenção usa uma
   única chave UUID. Telefone aceita até 40 caracteres com dígitos ASCII, `+`,
   espaços, hífen e parênteses; é canonicalizado antes de calcular a identidade
   do pedido. Não aceita JID, LID, texto, URL ou parâmetros de transporte.
2. Vazio resolve para o PN próprio verificado atual. Um telefone explícito igual
   ao PN próprio também é congelado como modo `self`; outro telefone é `explicit`.
   A intenção congela ator, dispositivo, geração, revisão, PN próprio,
   destinatário e texto. Expira em **120 s**. Não cria evento, efeito ou envio.
3. O navegador apresenta o destinatário efetivo e o texto. Cancelar descarta
   apenas o identificador local de uma prévia conhecida. Preparações abandonadas
   expiram e não bloqueiam novas preparações. Sem endpoint de cancelamento.
4. `POST /{device_id}/test-messages/{request_id}/confirm` (202), corpo
   `{idempotency_key,confirmation_version,confirmation:"ENVIAR"}`. É a **mesma**
   chave do prepare. Não recebe telefone/texto. Reconsulta PN verificado, geração,
   revisão e autorização, então cria exatamente um evento interno `diagnostic`
   e um efeito `send_text` de propósito `diagnostic_test`, com prazo de execução
   de 120 s. Não compila regra nem executa ASR, HTTP, webhook ou resposta automática.
5. O broker atual autoriza o efeito e faz recheck SQL exclusivo deste propósito
   imediatamente antes do relay. A ponte confirma novamente o PN próprio atual
   em ambos os modos, após lookup/preparação e antes do relay. A exceção de
   self-send exige modo `self`, confirmação e PN congelado igual ao PN atual.
   Nenhuma flag pública habilita envio para si, e o `send_text` comum não muda.

Uma execução admitida por espécie/dispositivo e uma sequência em andamento para
o mesmo destinatário bloqueiam nova admissão. Uma preparação não é execução.
O efeito terminal original permanece intacto. Uma tentativa nova requer nova
ação e confirmação explícitas; um resultado incerto nunca provoca reenvio.

## Recuperação sem conteúdo privado no navegador

O navegador pode conservar tenant_id, account_scope (SHA256 da identidade), UUID
da chave, device_id, kind e expected_version: somente identificadores e hashes
para vincular a recuperação à mesma conta e empresa. Telefone e texto não vão
para history/sessionStorage. `GET /requests/{key}` devolve a prévia
persistida ou o efeito, sempre reautorizando a pessoa, empresa e nicho.

Uma confirmação já admitida retorna o mesmo efeito mesmo após a versão mudar;
primeiro valida a identidade do pedido e a autorização atual. Repetir uma chave
com pedido diferente retorna409 `DIAGNOSTIC_KEY_CONFLICT`. Repetir confirmação
exige gesto explícito e a mesma chave; não ocorre automaticamente.

Se GET retorna404, a UI conserva a chave e não inventa intenção nova. Se a
preparação nunca chegou ao servidor, o usuário pode reapresentar o telefone e
preparar explicitamente com a mesma chave. Se a intenção existe, sua recuperação
dispensa reconstruir qualquer dado privado do navegador. Uma preparação
expirada permanece expirada; uma intenção nova requer ação explícita.

## Operação pública

Campos comuns:

```text
request_id, idempotency_key, kind: message_test|proxy_test, device_id,
status: prepared|pending|running|succeeded|failed|uncertain|expired,
replayed, created_at, expires_at, finished_at,
preview, message, proxy
```

- `preview` é nulo para proxy; para mensagem contém
  `{confirmation_version,recipient:{mode:self|explicit,phone_e164},text,expires_at}`.
  Sua expiração continua sendo a da preparação mesmo depois da confirmação.
- `message` é nulo antes de confirmar. Depois contém
  `{effect_id,status:pending|sent|failed|uncertain,outcome,error_code,reason}`.
  O estado é derivado do ledger de efeitos, sem UPDATE em GET. `succeeded` no
  envelope corresponde a `message.outcome=accepted`, não entrega.
- `proxy` contém
  `{status:reachable|failed|uncertain|unavailable,scope:"configured",configuration_version,applied_to_session,latency_ms,observed_at,error_code,reason}`.
  Um resultado indisponível não é um proxy comprovadamente ruim.

## Proxy: configuração testada e aplicação são diferentes

`POST /{device_id}/proxy-tests` (202) recebe só
`{idempotency_key,expected_version}`. Testa a configuração atual autorizada,
inclusive sem WhatsApp conectado ou com revisão ainda não aplicada, por transporte
efêmero. Não cria/reinicia sessão WA. A UI diferencia a configuração testada de
`applied_to_session`. Conexão direta é rotulada como tal, sem tráfego de teste.

A ponte recebe uma autorização própria de operação, independente de lease WA.
Claim tem owner UUID, token UUID, fence e prazo de no máximo30 s; a intenção tem
60 s. Recheck não renova o prazo nem faz takeover. O alvo fixo de TLS é
`www.cloudflare.com:443`; DNS/endereço público, CONNECT para IP fixado, SNI e
certificado são verificados na ponte. O probe tem prazo total de10 s e limita
cabeçalhos de CONNECT a8KiB. A latência do proxy mede CONNECT+TLS após DNS e
recheck; não é RTT do WhatsApp. A aplicação à sessão é um snapshot observado
pela instância da ponte, com reautorização final da configuração.
Não há URL pública controlada pelo POST,
redirect, HTTP para cliente, mensagem ou impressão de credenciais.

Uma chave nunca repete um probe. Reserva órfã ou perda de resultado produz
incerteza. Mudança de configuração durante execução não é apresentada como saúde
atual. O resultado significa apenas conectividade TLS ao alvo fixo por aquele
proxy; não prova conexão do WhatsApp nem entrega de mensagem.

## Protocolo privado

API → ponte, com credencial privada do canal:

- `POST /internal/channels/{channel_id}/device/diagnostics/proxy`, corpo
  `{request_id,device_id,generation,settings_revision}`; resposta202, execução
  em background após claim SQL e reserva durável.
- `GET /internal/channels/{channel_id}/device/diagnostics/operations/{request_id}`:
  lê somente recibo/ledger. GET público pode projetar o recibo em memória,
  sem reconciliação SQL ou repetição de I/O.

Ponte → API, prefixo `/internal/qr/channels/{channel_id}/device/diagnostics`:

- `POST /proxy/authorize`: scope acima + `owner,claim_token,recheck:false`.
  Recheck usa `recheck:true,claim_fence`. Grant contém scope, tipo, hash da
  configuração, claim/fence, `expires_at,expires_in_ms`, `proxy` e `proxy_secret`
  apenas privados. Repete owner/nicho/feature/geração/revisão antes da rede.
- `POST /proxy/finish`: scope/claim completo + `status:succeeded|failed|uncertain`
  e resultado estrito; ACK atrasado pode registrar prova produzida no prazo do
  claim, sem conceder qualquer execução nova.
- `POST /proxy/receipt`: `{request_id}`; apesar do método privado, concede apenas
  leitura, retorna `read_only:true` com scope, sem writes, lease ou claim novo.

Os callbacks privados `finish` e `receipt` possuem uma transação exclusiva para
preservar evidência de uma intenção já autenticada quando a empresa passa a
`paused`. Essa exceção é limitada a esses dois paths e a esse estado: mantém o
nicho atual, a associação ativa do proprietário, a credencial e o canal canônico,
e os vínculos de empresa, intenção, dispositivo e ator. `finish` também exige o
claim/token/fence original e prova produzida dentro de seu prazo. Não devolve
configuração, senha ou autorização de rede, e não cria/renova claim.

Uma empresa ativa continua sujeita a `devices.manage` e `diagnostics.manage`;
estados diferentes de `active` e `paused` são recusados. `authorize` e recheck
permanecem na guarda original, que recusa a empresa pausada antes de conceder rede
com 403 `WORKSPACE_FEATURE_UNAVAILABLE`. A recuperação pública também continua
sujeita às permissões atuais; a exceção é somente dos callbacks privados de
preservação de evidência. Uma mudança de proprietário, nicho ou canal nunca é
dispensada por essa exceção.

Resultado privado:
`{scope:"configured_proxy",configuration_version,applied_to_session:boolean|null,state:reachable|not_configured|failed|uncertain,latency_ms,target:"tls_reference",observed_at,error_code}`.
Não aceita campos extras, exceção bruta, URL, host escolhido ou credenciais.

## Persistência e códigos

Migração `0066_subscription_diagnostics`, após0065. Tabela
`subscription_diagnostic_requests`: tenant/device/generation/revision/actor,
kind/key/fingerprint, snapshot/confirmation_version imutáveis, prazos, lifecycle,
effect_id único opcional, claim próprio de proxy e resultado limitado.
RLS ENABLE+FORCE; runtime sem SELECT/INSERT/UPDATE/DELETE; suporte SELECT/INSERT
e UPDATE somente das colunas de ciclo de vida. FKs compostas impedem vínculos
entre empresas. Snapshot não duplica senha do proxy. Não altera evidência anterior.

Erros públicos seguros:

| HTTP | Códigos |
| --- | --- |
|404|`DIAGNOSTIC_NOT_FOUND`|
|410|`DIAGNOSTIC_PREVIEW_EXPIRED`|
|409|`DIAGNOSTIC_CONTEXT_CHANGED`, `DIAGNOSTIC_KEY_CONFLICT`, `DIAGNOSTIC_CONFIRMATION_INVALID`, `DIAGNOSTIC_DEVICE_UNAVAILABLE`, `DIAGNOSTIC_SELF_IDENTITY_UNVERIFIED`, `DIAGNOSTIC_SELF_IDENTITY_CHANGED`, `DIAGNOSTIC_BUSY`, `DIAGNOSTIC_CONTACT_BUSY`, `DIAGNOSTIC_REVOKED`, `PROXY_NOT_CONFIGURED`|

Autorização pode retornar401/403 antes de ler corpo. JSON é limitado a4096bytes,
sem chaves repetidas; conteúdo inválido retorna422 sem ecoar dados privados.

Códigos de resultado proxy permitidos: `PROXY_NOT_CONFIGURED`,
`PROXY_CONFIGURATION_INVALID`, `PROXY_DESTINATION_REFUSED`,
`PROXY_TARGET_UNAVAILABLE`, `PROXY_AUTH_FAILED`, `PROXY_CONNECT_REFUSED`,
`PROXY_TLS_FAILED`, `PROXY_TIMEOUT`, `PROXY_ABORTED`, `PROXY_CONFIGURATION_CHANGED`,
`PROXY_AUTHORIZATION_REVOKED`, `PROXY_PROBE_FAILED`, `PROXY_OUTCOME_UNCERTAIN` e
`PROXY_EXPIRED_BEFORE_START` (derivado da ausência de claim).

A fixture sintética compartilhada é
`tests/fixtures/subscription_diagnostics_contract.json`. Testes e CI não usam
dados de clientes nem fazem mensagens/probes reais.
