# Google Agenda: conexão e sincronização do Orion

O PostgreSQL do Orion permanece a fonte dos horários, conflitos, estados e
histórico. A integração envia alterações **do Orion para um calendário Google
escolhido explicitamente pelo proprietário**. Não importa eventos do Google,
não calcula disponibilidade pela agenda Google e não transforma edições feitas
no Google em alterações no Orion.

## Dados e estados enviados

| Situação no Orion | Resultado no calendário escolhido |
| --- | --- |
| Reserva temporária (`hold`) ou expiração | Nenhum evento novo. |
| Confirmado | Evento privado `Confirmado · cliente · serviço`, cor 9. |
| Remarcado | O mesmo evento recebe os horários atuais. |
| Finalizado | Título `Finalizado · cliente · serviço`, cor 10. |
| Não compareceu | Título `Não compareceu · cliente · serviço`, cor 11, transparente para não bloquear o horário no Google. |
| Cancelado pelo cliente, profissional ou negócio | O evento correspondente é removido; solicitante, autor, motivo e histórico continuam no Orion. |

O título usa nome útil e limitado, serviço e, quando necessário, protocolo.
Nomes genéricos como `Cliente WhatsApp` e números não são exportados como
identificação. A descrição inclui profissional e protocolo com limites e escape
de HTML. Horários incluem offset e fuso do estabelecimento.

Telefone, e-mail, notas internas, motivo do cancelamento, conversas e histórico
de operadores não compõem o evento. Não há convidados nem lembretes padrão;
as mutações usam `sendUpdates=none`. Se alguém acrescentar convidados a um
evento gerenciado, o worker interrompe a alteração e pede revisão.

`completed` e `no_show` são estados do Orion representados por título, cor e
propriedades privadas. Não são estados nativos adicionais do recurso Google;
esses eventos permanecem `confirmed` no campo `status` do Google.

### Janela inicial

Na primeira ativação entram os atendimentos confirmados atuais e futuros
(`end_at >= now()`). Intenções antigas, encerradas antes da conexão, sem vínculo
externo e sem tentativa de envio ficam fora da sincronização inicial. O banco
retém essas intenções com marcador `google_before_connection`; o painel mostra
`outside_initial_sync`, sem erro nem sucesso de envio. Elas não entram nos
totais de pendências/falhas nem em **Repetir sincronização**.

Atualizações/cancelamentos de eventos com vínculo são preservados. Tentativas de
resultado incerto continuam recuperáveis pelo identificador determinístico,
mesmo sem confirmação de sucesso gravada. Reconectar a mesma agenda não descarta
essas tentativas. Um atendimento excluído da janela que for remarcado para um
horário atual/futuro pode voltar à fila. Não há exportação integral do histórico.

## Configuração

Criar um cliente OAuth **Web application**, habilitar a Google Calendar API e
configurar a tela de consentimento para o público real do produto. Escopos:

```text
https://www.googleapis.com/auth/calendar.events
https://www.googleapis.com/auth/calendar.calendarlist.readonly
```

O primeiro permite escrever eventos; o segundo permite listar agendas e resolver
seu ID canônico com `calendarList.get`. Só são oferecidos papéis `owner` e
`writer`; leitura e `writerWithoutPrivateAccess` não servem para os eventos
privados desta integração. A listagem é paginada, com limite de mil calendários.

| Variável | API | Worker | Regra |
| --- | --- | --- | --- |
| `GOOGLE_CLIENT_ID` | Obrigatória | Obrigatória | Mesmo cliente OAuth. |
| `GOOGLE_CLIENT_SECRET` | Privada, obrigatória | Privada, obrigatória | Somente no servidor. |
| `GOOGLE_REDIRECT_URI` | Obrigatória | Não usada | Callback HTTPS exato, sem query/fragmento. |
| `INTEGRATION_FERNET_KEY` | Privada, obrigatória | Mesma chave privada | Cifra refresh tokens; preservar uma chave existente. |
| `GOOGLE_LIVE_SYNC` | `1` para retry e indicador habilitado | `1` para processar a fila | Ausente/diferente de `1` desabilita envio. |
| `REDIS_URL` | Sessões e intenções OAuth | Fila ARQ | Infraestrutura operacional existente. |
| `PANEL_PUBLIC_URL` | Origem pública, quando definida | Não usada pelo Google | Deve coincidir com a origem do callback. |

Callback deste ambiente público:

```text
https://orion-assistente-production.up.railway.app/integrations/google/callback
```

Registrar a URL exata nas **Authorized redirect URIs** do cliente Google, não
`/panel`. O hostname aberto pelo usuário deve coincidir com o callback para o
cookie de verificação de navegador funcionar.

Não colocar segredo, `client_secret.json`, código de autorização, refresh token
ou chave Fernet no repositório, navegador, exemplos de logs ou atendimento ao
usuário. O fluxo usa código de autorização com PKCE, `access_type=offline` e
consentimento explícito. Exige os dois escopos e refresh token na resposta.

`INTEGRATION_FERNET_KEY` também governa integrações existentes, inclusive Meta.
Antes de introduzi-la numa instalação sem chave, avaliar essas capacidades.
Se já existem credenciais cifradas, preservar a chave: alterá-la sem recifragem
invalida essas credenciais. Esta entrega não implementa rotação nem altera flags
Meta. API e worker precisam receber a mesma chave estável.

O launcher da API desabilita o access log Uvicorn para não registrar `code` e
`state` da query do callback, preservando logs de erro e da aplicação. Antes de
ativar OAuth real, conferir também logs de proxy/plataforma. O handler não registra
nem devolve esses valores e redireciona sem eles; isso não sanitiza logs mantidos
por outro componente.

No levantamento desta entrega, os nomes `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET` e `INTEGRATION_FERNET_KEY` estavam ausentes na API e no
worker. Nenhuma credencial Google real foi criada ou usada no desenvolvimento.
A presença isolada do callback não ativa a integração. `configured:false` é o
resultado esperado enquanto faltarem os requisitos da API; o worker valida seus
próprios requisitos antes de processar qualquer intenção.

## Fluxo e permissões

Somente o proprietário atual da empresa, suporte ou administrador pode conectar,
escolher calendário, desconectar ou pedir retry. Editor e leitor consultam o
estado. A autoridade vem da sessão e da associação atual à empresa.

1. O usuário abre Google Agenda no Orion e confirma **Conectar**.
2. `POST /google/connect` valida sessão, CSRF/origem e autoridade. Cria state de
   uso único, PKCE e cookie opaco HTTPS/HttpOnly/SameSite=Lax, limitado ao callback.
   A intenção Redis dura dez minutos e vincula empresa e iniciador.
3. O callback valida navegador, intenção, sessão/conta e permissão antes e depois
   da troca. Logout, mudança da versão da conta, revogação ou perda de acesso
   impedem o uso posterior da autorização.
4. A credencial cifrada fica temporariamente no Redis. Ainda não existe nova
   conexão ativa nem envio de evento. O callback retorna para
   `/panel?aba=agenda&google=choose&empresa=<uuid>`.
5. O usuário escolhe uma agenda editável. O servidor comprova seu ID canônico,
   revalida autoridade e versão da conexão, consome a seleção uma vez e persiste
   o refresh token cifrado sob RLS. O worker pode enviar se a flag estiver ativa.

Tokens de acesso são usados apenas no servidor. O retorno ao painel contém
estado, calendário e capacidades. Abrir a página, executar GET ou interagir com
a IA não inicia conexão.

### Reconexão e desconexão

Após a primeira ativação, o ID canônico permanece vinculado à empresa, inclusive
após desconectar. Reconexão renova a autorização dessa agenda; não migra nem
limpa eventos de outra. Uma nova conta Google precisa ter edição no calendário
já vinculado.

Conexões legadas com `primary` comprovam o ID canônico usando a credencial
anterior. Nunca se interpreta esse alias somente com a conta nova. Se a
credencial antiga estiver ausente, expirada ou sem escopo para comprovar o
calendário, a conversão exige revisão operacional.

**Desconectar** remove o refresh token e invalida autorizações temporárias, mas
conserva vínculo e eventos enviados. Não remove reservas do Orion nem revoga
globalmente o acesso do projeto Google à conta. Para essa última ação, o titular
pode remover o acesso nas configurações da própria conta Google.

Um envio em andamento mantém um lock; desconectar retorna conflito até ele
terminar. Depois de uma desconexão bem-sucedida, o worker não reutiliza a
credencial removida.

## Estado e endpoints

Prefixo: `/api/v1/tenants/{tenant_id}/google`.

| Método e caminho | Efeito |
| --- | --- |
| `GET /status` | Estado local, fila, capacidades e seleção pendente; não lê eventos Google. |
| `POST /connect` | Intenção OAuth e `authorization_url`. |
| `GET /calendars` | Agendas editáveis da autorização temporária, com `selectable` explícito. |
| `POST /calendar` | Recebe `{calendar_id}` e ativa a agenda após validação. |
| `POST /revoke` | Desconecta localmente; conserva eventos existentes. |
| `POST /retry` | Reabre intenções pendentes/com falha elegíveis; uma solicitação por minuto. |
| `GET /appointments/{appointment_id}/status` | Situação da intenção do atendimento, isolada por empresa. |

A conexão retorna `configured`, `connected`, `status`, `sync_enabled`,
`sync_status`, `calendar_id`, `calendar_name`, `last_synced_at`, `pending`,
`failed`, `sent`, `last_error`, `selection_pending`, `calendar_change_locked`,
`direction:"orion_to_google"` e `capabilities` com `can_connect`,
`can_disconnect`, `can_select_calendar`, `can_retry`.

`configured` valida a configuração local da API, não testa a conexão Google nem
prova a configuração do worker. `connected` indica autorização ativa salva e
ID canônico, não sucesso de todas as entregas. `sync_status` distingue
`not_configured`, `not_connected`, `choose_calendar`, `paused`,
`reauthorization_required`, `pending`, `attention`, `up_to_date`, `idle`.

O atendimento pode estar `not_queued`, `outside_initial_sync`, `pending`,
`retrying`, `synced` ou `failed`. Também informa configuração, conexão,
tentativas, próximo vencimento e erro seguro. Totais representam a intenção
atual por atendimento, não requisições cumulativas. `last_synced_at` vem das
intenções atualmente enviadas; uma atualização pendente não aparece como enviada.

Uma entrega reconhecida é a última observação do Orion. Não há polling contínuo
de edições remotas, importação Google→Orion, webhook Google nem garantia de
detectar uma edição manual antes da próxima intenção.

## Fila, falhas e idempotência

`enqueue_calendar_sync` participa da mesma transação da alteração do atendimento.
Há uma outbox por empresa/agendamento, preservando ID externo e combinando
intenções consecutivas. O worker lê o estado atual antes de enviar, inclusive
`completed`/`no_show`, sem depender de novos campos do histórico.

O ID externo é determinístico a partir do UUID. Antes de alterar/apagar um evento,
o provider confere propriedades privadas de agendamento/empresa e ausência de
convidados. Um 409 após criação incerta exige leitura e aplicação do estado atual,
não é sucesso cego. ETags protegem concorrência. Um 404 na exclusão só significa
ausência após comprovar acesso ao calendário, pois pode ocultar perda de acesso.

O scanner ARQ seleciona até cem intenções vencidas a cada cinco minutos. Falhas
transitórias têm espera exponencial e limite de oito tentativas. A espera real
pode superar o backoff, conforme a próxima rodada. A operação remota do worker
tem limite total de 25 segundos.

Operações Google são serializadas por empresa. Se outra intenção encontra a
conexão ocupada, ganha vencimento em trinta segundos, sem consumir tentativa.
A chave ARQ inclui o vencimento e permite reenfileirar mesmo com resultado
anterior retido. Uma empresa desconectada recebe espera de quinze minutos, sem
esgotar tentativas nem monopolizar as primeiras vagas da fila.

Quota, indisponibilidade e concorrência são separadas da perda de autorização.
`invalid_grant`, 401 e perda de permissão pedem reconexão; evento de terceiros ou
com convidados pede revisão. Banco/API guardam códigos finitos, como
`google_rate_limited`, `google_authorization_expired`, `google_event_not_owned`,
nunca corpo da resposta, URL com token ou texto da exceção. Falha externa não
cancela nem desfaz um atendimento no Orion.

## Validação reproduzível

```bash
python -m pytest -q tests/unit/test_google_calendar_unit.py \
  tests/integration/test_google_calendar_integration.py \
  tests/integration/test_google_csv.py
```

Integrações exigem PostgreSQL com migrations atuais e papéis distintos em
`APP_DATABASE_URL`, `MIGRATION_DATABASE_URL`, `SUPPORT_DATABASE_URL`. Usam Redis
em `ORION_GOOGLE_TEST_REDIS_URL` ou `REDIS_URL`. As flags
`REQUIRE_POSTGRES_TESTS=1` e `REQUIRE_REDIS_TESTS=1` impedem que ausência dos
serviços pareça validação concluída. Dados e credenciais são sintéticos; Google
usa `httpx.MockTransport` ou provider local, sem conexão externa.

O gate cobre PKCE/cookie/CSRF/state, perda de sessão/permissão durante a troca,
seleção explícita, reconexão canônica/legada, revogação concorrente, janela
inicial, estados, RLS, backoff e duas intenções da mesma empresa em ARQ real com
resultado retido. Não valida consentimento, permissões do projeto/Workspace nem
callback na infraestrutura pública. Esse aceite depende da configuração real
e de uma conexão deliberada do proprietário.

O CSV autenticado permanece em
`GET /api/v1/tenants/{id}/export/appointments.csv`, com isolamento de empresa,
UTF-8 BOM, separador `;` e neutralização de fórmulas.

## Fontes oficiais consultadas

- [OAuth web, offline e consentimento](https://developers.google.com/identity/protocols/oauth2/web-server).
- [Flow web oficial, code_verifier e PKCE](https://googleapis.dev/python/google-auth-oauthlib/latest/reference/google_auth_oauthlib.flow.html).
- [CalendarList.list: paginação e permissões](https://developers.google.com/workspace/calendar/api/v3/reference/calendarList/list).
- [CalendarList.get: ID canônico e alias primary](https://developers.google.com/workspace/calendar/api/v3/reference/calendarList/get).
- [Event: status, privacidade e transparência](https://developers.google.com/workspace/calendar/api/v3/reference/events).
- [Events.insert e sendUpdates](https://developers.google.com/workspace/calendar/api/v3/reference/events/insert).
- [Events.patch e campos omitidos](https://developers.google.com/workspace/calendar/api/v3/reference/events/patch).
- [Events.delete](https://developers.google.com/workspace/calendar/api/v3/reference/events/delete).
- [Erros 404, 409, 410, quota e backoff](https://developers.google.com/workspace/calendar/api/guides/errors).
