# Etapa 08 — contrato de Mensagens Programadas

Contrato de integração v1, 2026-11-05. Este documento fixa os nomes comuns
entre API, banco, runtime e interface. A observação está em
scheduled-messages-observation.md. As regras de execução abaixo são decisões
explícitas do Orion para os comportamentos não exercitados na referência.

## Escopo e identificação

- Nicho persistido: servico_assinaturas; workspace: subscription_services.
- Proprietário atual da empresa ativa; permissão: scheduled_messages.manage.
- Rota do painel: programadas. Componentes, tipografia e cores existentes.
- API: /api/v1/tenants/{tenant_id}/subscription-whatsapp/scheduled-messages.
- Evento interno: scheduled. Tipo e purpose do efeito: scheduled_action.
- Sem escrita, admissão, consulta à ponte ou envio nos GETs.
- Nenhum novo complemento pago; não pressupõe paridade de comportamento privado.

## Definição recebida do formulário

    {
      "title": "Aviso de renovação",
      "device_id": "uuid",
      "group_ids": ["uuid"],
      "contact_keys": ["phone:5567999999901"],
      "all_contacts": false,
      "start_date": "2030-01-10",
      "start_time": "14:00",
      "interval_seconds": 60,
      "repeat_every_30_days": false,
      "message_type": "text",
      "text": "Olá, {name}.",
      "template_id": null
    }

Título não vazio até 120 caracteres. Exatamente um dispositivo. Grupos sem
repetição, até 100; contato direto é uma chave phone seguida de 8–15 dígitos
internacionais, resolvida novamente pelo servidor. all_contacts=true substitui
contact_keys e resolve o catálogo completo na revisão, sem criar seleção viva.
É necessário grupo ou contato; audiência final até 10.000 telefones distintos.

Data mínima é hoje no fuso persistido da empresa. Horário HH:MM; intervalo
pertence a [10,20,30,40,50,60]. Texto próprio tem até 1.000 unidades UTF-16,
usa somente {name}, não aceita campos/formatação adicionais nem expansão
recursiva. O limite também vale depois da personalização. Template substitui
text, usa a biblioteca existente e seus oito formatos/limites.

Timezone não é recebido do navegador. O servidor lê o IANA do tenant e o
acrescenta à definição congelada. Mudança entre revisão e confirmação invalida
a revisão. Depois da confirmação, o fuso permanece o confirmado.

Padrões devolvidos por options: amanhã, 14:00, intervalo 60, repetição false,
message_type text. A estimativa é início + N * intervalo, inclusive N=1;
é estimativa mínima, não promessa de duração/entrega. O espaçamento real
começa após o término da sequência anterior, sem rajada de compensação.

## Contatos, nomes e autorização

O catálogo próprio é a união SQL de customers ativos, não excluídos e com
telefone, com membros não removidos de grupos Campanhas não arquivados.
Consultas e joins sempre incluem tenant_id. Não utiliza GET de management,
que inclui outros dados e pode consultar a ponte.

Deduplicar por telefone canônico sem +. Nomes aceitam até 120 caracteres,
sem corte: nome útil de cliente tem prioridade; depois membro de grupo com
desempate estável por group_id; telefone é a identificação visual de fallback.
Não inferir país, identidade por nome ou JID/LID. A presença no catálogo não
concede consentimento. A revogação em subscription_campaign_permissions
prevalece e sua versão é congelada e revalidada.

Cada destinatário guarda origins, um array JSONB de fontes exatas:

    [
      {"kind":"customer","id":"uuid","phone":"5567999999901","version":1},
      {"kind":"group_member","group_id":"uuid","phone":"5567999999901",
       "created_at":"ISO UTC","updated_at":"ISO UTC"}
    ]

customer.version é a versão persistida; vínculo de membro não possui versão
numérica e usa os timestamps persistidos. A revisão também captura a versão
do grupo para detectar alterações antes da confirmação. A execução exige ao
menos uma origem congelada ainda válida, sem redirecionar telefone nem admitir
uma fonte nova. Opt-out e invalidações após claim permanecem duráveis.
Cada telefone possui no máximo 101 origens: um customer pela unicidade de
telefone por empresa, mais até 100 grupos ativos. O próprio telefone canônico
do dispositivo, quando runtime.phone_verified=true, permanece visível na
contagem mas é bloqueado na revisão e não é admitido para envio a si mesmo.

## Revisão e confirmação, sem rascunho extra

POST /review prepara criação; POST /{schedule_id}/review prepara edição de
ocorrência ainda não admitida. Nenhuma série/ocorrência é criada nessa fase.
A revisão persiste por 120 segundos e congela definição, contexto, fontes,
permissões e planos. A confirmação usa a mesma chave e o mesmo review_version.

Criação:

    {"idempotency_key":"uuid","schedule":{...definição...}}

Edição:

    {"idempotency_key":"uuid","run_id":"uuid","expected_version":1,
     "expected_run_version":1,"schedule":{...definição...}}

Confirmação em POST /confirm:

    {"idempotency_key":"a mesma uuid","review_version":"sha256",
     "confirmation":"AGENDAR","consent_confirmed":true}

Uma confirmação válida cria ou atualiza a série, cria/atualiza sua ocorrência
não admitida e congela seus destinatários. Uma ocorrência com admissão
anterior não pode ser editada. Repetição da mesma confirmação devolve o
resultado original; chave com outro payload é conflito. GET /requests/{key}
recupera a operação, nunca repete a mutação. Revisão expirada requer outra
revisão explícita; recuperação negativa não é armazenada como resultado.

## Modelo SQL comum

Todas as quatro tabelas possuem id UUID, tenant_id, created_at. RLS forçada,
sem grants de conteúdo para app_runtime; app_support com SELECT/INSERT e
UPDATE apenas nas colunas operacionais necessárias. Sem DELETE de histórico.
Índices e FKs compostas impedem vínculos entre empresas.

### subscription_schedules

- id, tenant_id, owner_account_id, title varchar(120), version bigint > 0.
- status: active / cancelled / completed.
- definition JSONB; snapshot JSONB de metadados; run_version char(64).
- device_id UUID; template_id UUID nullable; template_revision bigint nullable.
- timezone varchar(64); anchor_date date; anchor_time char(5).
- interval_seconds smallint em 10/20/30/40/50/60; repeat_every_30_days boolean.
- confirmed_at, cancelled_at nullable, created_at, updated_at timestamptz.
- UNIQUE(tenant_id,id); vínculo composto ao dispositivo/template quando presente.
- definition e snapshot são objetos; cada um até 1 MiB. Snapshot não contém
  o array de contatos nem todos os planos personalizados.

### subscription_schedule_runs

- id, tenant_id, schedule_id UUID; occurrence_index integer >= 0.
- schedule_version bigint > 0; version bigint > 0; run_version char(64).
- status: pending / running / completed / failed / uncertain / cancelled.
- due_at timestamptz nullable somente quando pending ou cancelled com
  error_code=SCHEDULE_RECURRENCE_INVALID; local_date date; timezone varchar(64).
- snapshot JSONB de metadados imutáveis, até 1 MiB, com definition, device,
  template, facts, groups, consent e review_request_id; sem contatos.
- first_admitted_at, last_admitted_at, next_admission_at, manual_released_at,
  finished_at nullable; error_code varchar(80) nullable; created_at, updated_at.
- source_run_id UUID nullable para recorrência.
- UNIQUE(tenant_id,id), UNIQUE(tenant_id,schedule_id,id),
  UNIQUE(tenant_id,schedule_id,occurrence_index).
- FK(tenant_id,schedule_id) para schedules; source_run_id restrito ao mesmo
  tenant/schedule. Primeira ocorrência tem índice 0.
- pending/running sem finished_at; terminais com finished_at. Mutação de
  status/admissão incrementa version. Metadados/índice de uma ocorrência
  admitida nunca são reconstruídos a partir de catálogo novo.

### subscription_schedule_recipients

- id, tenant_id, schedule_id, run_id UUID; run_version char(64).
- ordinal integer 1–10000; phone varchar(15) canônico; name varchar(120).
- origins JSONB array não vazio; permission_version bigint >= 0.
- device_id, generation UUID; settings_revision bigint > 0.
- plan JSONB array com 1–3 ações, até 131072 bytes; plan_hash char(64).
- status: pending / running / sent / failed / uncertain / cancelled.
- admitted_at, finished_at nullable; error_code varchar(80) nullable; created_at.
- UNIQUE(tenant_id,id), UNIQUE(tenant_id,schedule_id,run_id,id),
  UNIQUE(tenant_id,run_id,run_version,phone),
  UNIQUE(tenant_id,run_id,run_version,ordinal).
- FK composta para run e FK(tenant_id,device_id,generation) para dispositivo.
- Total expandido de destinatários + planos por ocorrência <= 64 MiB;
  validar o orçamento antes de gravar a audiência.
- pending/running sem finished_at; terminais com finished_at.
  running/sent/uncertain exigem admitted_at.
- Edição antes de qualquer admissão cancela a audiência antiga, conserva seus
  registros e cria nova audiência com outro run_version. Projeções/execução
  filtram recipient.run_version=run.run_version; não há DELETE nem reescrita
  de planos antigos. O request confirmado conserva o snapshot anterior.

### subscription_schedule_requests

- id, tenant_id, actor_account_id, idempotency_key UUID.
- kind: review_create / review_edit / pending_review / cancel_run / cancel_series.
- request_fingerprint char(64); schedule_id, run_id UUID nullable.
- status: prepared / succeeded; review_version char(64) nullable.
- snapshot JSONB nullable (revisão completa, até 64 MiB); preview JSONB nullable
  até 1 MiB; result JSONB nullable até 4 MiB.
- created_at, expires_at, confirmed_at nullable.
- UNIQUE(tenant_id,idempotency_key), UNIQUE(tenant_id,id).
- prepared exige snapshot, preview, review_version e expires_at. O status
  expired é uma projeção de leitura; não é uma escrita feita pelo GET.
- O ator original precisa coincidir mesmo após troca de proprietário.

### Colunas compartilhadas de efeito

Acrescentar em subscription_device_effects schedule_id, schedule_run_id,
schedule_recipient_id UUID e schedule_part_index smallint 0–2.
Todos não nulos somente em type=purpose=scheduled_action; todos nulos nos
outros tipos. FKs compostas para série/ocorrência/destinatário; unicidade
(tenant_id,schedule_recipient_id,schedule_part_index). Preservar os guards
anteriores de Campanhas/Chatbot. Permitir evento scheduled na whitelist.

## Snapshot de metadados e destinatários

    {
      "definition": {...inclui timezone...},
      "device": {"id":"uuid","name":"Nome","generation":"uuid",
        "settings_revision":1,"channel_id":"uuid","owner_account_id":"uuid"},
      "template": null,
      "facts": {"my_name":"Empresa","my_email":"conta@example.invalid",
        "my_contact_number":""},
      "groups": [{"id":"uuid","version":1,"name":"Grupo","member_count":2}],
      "review_request_id":"uuid",
      "consent":{"confirmed":true,"actor_account_id":"uuid",
        "review_request_id":"uuid","confirmed_at":"ISO UTC"}
    }

Revisão acrescenta contacts com phone/name/origins/permission_version/blocked/
plan/plan_hash. Ao confirmar, contacts vira a tabela recipients e é excluído
do snapshot da série/ocorrência. A recorrência clona os destinatários/planos
da ocorrência anterior, não faz nova união de catálogo. A revogação continua
sendo revalidada para cada destinatário.

## Recorrência e atraso

- Repetição: data âncora + 30*k dias locais, no horário/fuso congelados; não
  equivale a mês civil nem a término + 30 dias.
- Materializar no máximo a próxima ocorrência futura. Não criar
  retrospectivamente os ciclos perdidos nem enfileirar rajada de compensação.
- A ocorrência de dia anterior ainda não admitida fica em Pendência passada
  e não inicia automaticamente. Uma ocorrência já iniciada pode terminar
  depois da meia-noite, conservando espaçamento e autorização.
- manual_released_at só é gravado por confirmação explícita do botão global;
  due_at/local_date originais permanecem no histórico.
- Horário recorrente inexistente/ambíguo no DST não é deslocado: exige atenção.
  A política retorna erro específico; a ocorrência futura conserva local_date
  e due_at NULL com SCHEDULE_RECURRENCE_INVALID, sem inventar instante UTC nem
  alterar o resultado histórico anterior.
- Só materializar a próxima ocorrência depois de a última ficar terminal.
  Pendência passada sem liberação aguarda ação; cancel_run permite o próximo
  ciclo, cancel_series impede. Edição de ocorrência índice k recalcula a
  âncora como nova data - 30*k; o runtime usa anchor_date/anchor_time da série.

## Botão global e cancelamento

POST /pending/review recebe {"idempotency_key":"uuid"}. Seleciona somente
ocorrências pending devidas, ainda sem first_admitted_at, de séries ativas;
inclui dias anteriores, exclui futuras, running, uncertain e já liberadas.
Captura IDs/versões e o motivo de atenção. Revisão global limitada a 100 runs.
Sem candidatos, persiste revisão vazia com total=0 e runs=[] para recuperação
segura da mesma chave. A interface permite encerrá-la e desabilita confirmar;
o servidor também rejeita a confirmação vazia, sem qualquer efeito.

POST /pending/confirm:

    {"idempotency_key":"a mesma uuid","review_version":"sha256",
     "confirmation":"ENVIAR PENDENTES","consent_confirmed":true}

Confere o conjunto preparado sob lock de tenant, marca a liberação explícita
e devolve os IDs liberados. Não cria efeitos, não consulta nem chama a ponte.
O poll continua sendo a única admissão; falta de dispositivo/contexto não é
bypass. Mudança de versão invalida a revisão. Confirmação repetida é replay.

POST /{schedule_id}/actions:

    {"idempotency_key":"uuid","action":"cancel_run","run_id":"uuid",
     "expected_version":1,"expected_run_version":1,"confirmation":"CANCELAR"}

action também admite cancel_series, com run_id/expected_run_version nulos.
Cancela somente trabalho ainda pendente; não apaga recibos/resultados nem
repete partes. Cancelamento da série impede próximas ocorrências. Resultado
incerto permanece explícito. Sem pause/resume ou exclusão física.

## DTOs públicos

Todos os instantes são ISO-8601 com timezone. UUIDs strings; hashes SHA256
hexadecimal. Erros usam detail {code,message,...}; sempre Cache-Control no-store.

### Options e catálogo

GET /options:

    {"devices":[{"id":"uuid","name":"Aparelho","status":"connected",
       "available":true,"blocked_reason":null}],
     "templates":[{"id":"uuid","name":"Texto","kind":"text","revision":1,
       "available":true,"blocked_reason":null}],
     "groups":[{"id":"uuid","name":"Grupo","version":1,"member_count":2}],
     "timezone":"America/Sao_Paulo","today":"2030-01-09",
     "defaults":{"start_date":"2030-01-10","start_time":"14:00",
       "interval_seconds":60,"repeat_every_30_days":false,"message_type":"text"},
     "limits":{"title":120,"text_utf16":1000,"groups":100,"recipients":10000,
       "interval_seconds":[10,20,30,40,50,60],"review_seconds":120},
     "generated_at":"ISO UTC"}

GET /contacts?q=&page=1:

    {"items":[{"key":"phone:5567999999901","phone_e164":"+5567999999901",
       "name":"Contato","available":true,"blocked_reason":null,"version":"sha256"}],
     "total":1,"page":1,"page_size":30,"pages":1,
     "timezone":"America/Sao_Paulo","generated_at":"ISO UTC"}

GET /audience?group_ids=uuid,uuid&contact_keys=phone:digits,phone:digits&all_contacts=false
é uma contagem estreita sem efeitos: {total,direct,from_groups,duplicates,blocked,
eligible,generated_at}. Mesmos limites e predicados da revisão; nenhuma consulta
à ponte. Se a seleção exceder o tamanho seguro de query, usar POST /audience
com {"group_ids":[],"contact_keys":[],"all_contacts":false}; também é somente
leitura e não registra operação. O POST aceita start_date, start_time e
interval_seconds opcionais em conjunto; quando fornecidos, o servidor calcula
start_at e estimated_end_at no fuso IANA do tenant. A resposta sempre inclui
timezone, start_at e estimated_end_at (null se ausentes/sem audiência).

### Lista por ocorrência

GET /?tab=active|past_due|history|all&status=attention|queued|processing|upcoming|&q=&page=1.
status só é válido na aba active; q preservado ao trocar aba; limpar volta
para active, status vazio e q vazio.

    {"items":[...item...],"total":1,"page":1,"page_size":30,"pages":1,
     "counts":{"active":1,"past_due":0,"history":0,"all":1,
       "attention":0,"queued":0,"processing":0,"upcoming":1},
     "timezone":"America/Sao_Paulo","generated_at":"ISO UTC"}

Item comum da lista/detail/result:

    {"schedule_id":"uuid","run_id":"uuid","title":"Aviso","version":1,
     "run_version":1,"occurrence_index":0,"series_status":"active",
     "status":"pending","status_label":"Próximo","bucket":"active",
     "active_filter":"upcoming","attention_reason":null,
     "device":{"id":"uuid","name":"Aparelho","status":"connected"},
     "scheduled_at":"ISO UTC","local_date":"2030-01-10","local_time":"14:00",
     "timezone":"America/Sao_Paulo","interval_seconds":60,
     "repeat_every_30_days":false,"estimated_end_at":"ISO UTC",
     "progress":{"total":1,"processed":0,"sent":0,"pending":1,
       "failed":0,"uncertain":0,"cancelled":0,"percent":0},
     "actions":["edit","cancel_run","cancel_series"],"created_at":"ISO UTC"}

version é a versão numérica da série; run_version é a versão numérica de
otimismo da ocorrência no DTO público, distinta do hash SQL/permit.
scheduled_at e estimated_end_at podem ser null somente para recorrência com
horário local inexistente/ambíguo; o item fica attention com motivo explícito.
Histórico contém todas as ocorrências terminais, mesmo de séries recorrentes
ainda ativas. Pendência passada contém pending sem admissão de dia anterior
e sem liberação manual; futuro é upcoming; pending devido é attention até
admissão; running com partes em fila é queued e com parte autorizada processing.

GET /{schedule_id}?run_id=uuid devolve:

    {"schedule":...item...,"definition":...definição...,
     "message_preview":{"type":"text","text":"Olá, {name}.","caption":null,
       "followup":null,"template_name":null,"parts":[{"type":"text","text":"Olá, {name}."}]},
     "groups":[...],"recipients":...página de destinatários...}

GET /{schedule_id}/recipients?run_id=uuid&status=&q=&page=1 devolve uma página
com itens {id,ordinal,name,phone,status,status_label,error_code,parts}; phone
com +; parts contêm {id,type,status,error_code} sem tokens/segredos.
GET /{schedule_id}/runs?page=1 devolve a mesma página de itens da lista,
somente daquela série.

### Envelope de operação

    {"request_id":"uuid","idempotency_key":"uuid","kind":"review_create",
     "status":"prepared","schedule_id":null,"run_id":null,"replayed":false,
     "created_at":"ISO UTC","expires_at":"ISO UTC","result":null,
     "preview":{"review_version":"sha256","expires_at":"ISO UTC",
       "title":"Aviso","definition":{...},"total":2,"eligible":2,"blocked":0,
       "duplicates":1,"direct":1,"from_groups":2,"device":{...},
       "groups":[...],"message_preview":{...},
       "schedule":{"start_at":"ISO UTC","timezone":"America/Sao_Paulo",
         "estimated_end_at":"ISO UTC","repeat_every_30_days":false}}}

Confirmação/edição/cancelamento: status succeeded, preview null e
result {"schedule":...item...}; cancel_series pode retornar
{"schedule_id":"uuid","cancelled":true}. Revisão global usa kind pending_review,
preview {review_version,expires_at,total,runs:[...item...]}; confirmação global
retorna result {released:[{"schedule_id":"uuid","run_id":"uuid"}],total:N}.
Recuperação devolve o mesmo envelope, com replayed=true; revisão expirada
projeta status expired e preview null. Nunca guarda conteúdo em localStorage.

## Funções comuns entre agentes

Conflito final comprovado para chave ausente:
detail.code=SCHEDULE_CONFLICT e detail.rejection contém reason=stale_version,
idempotency_key, kind (review_edit/cancel_run/cancel_series), schedule_id,
run_id nullable, expected_version e expected_run_version nullable. Emitir
somente sob lock quando todas as versões esperadas são <= atuais e ao menos
uma é estritamente menor. A interface compara todos os campos com sua intenção;
versão futura, conflito genérico e revisão já persistida não recebem esse sinal.
Os itens também incluem attention_message nullable, tradução pública de
attention_reason; destinatários incluem error_message nullable. Os códigos
continuam separados das mensagens de apresentação. cancel_run também está
disponível durante running para revogar somente o trabalho ainda pendente;
edit continua exclusivo da ocorrência pending sem qualquer admissão anterior.

Política (subscription_schedule_policy.py):

- local_instant(day, clock, zone) -> datetime UTC.
- start_at(definition) -> datetime UTC.
- next_occurrence(definition, now, after_index=0) -> (index, local_date, due_at);
  due_at é None para DST inválido e o chamador persiste o erro explícito.
- estimate_completion(due_at, count, interval) -> datetime ou None.
- frozen_plan(definition, template, facts) -> list de ações.
- recipient_state(recipient, parts, now), progress(states).
- run_projection(run, now, device_available=True) -> {bucket,active_filter,
  status_label,attention_reason}; consulta pura.

Store (subscription_schedule_store.py) expõe FEATURE, fail, encode,
fingerprint, bounded_json, owner_transaction, clock, schedule_row, run_row,
template_snapshot e sources_valid(db,tenant,origins,phone). Fontes/projeções
não consultam ponte nem escrevem. O runtime pode usar SQL próprio de
materialização/clonagem, respeitando exatamente este schema.

Runtime (subscription_schedule_runtime.py):

- admit_due_schedules(db, device).
- authorize_schedule_effect(db, device, effect, owner, fence, payload).
- schedule_effect_authority(db, device, effect, now).
- after_schedule_effect_finish(db, device, effect, state).
- invalidate_schedule_contacts(db, tenant).
- cancel_schedule_pending(db, schedule, code, *, run_id=None,
  include_unadmitted=True).

Não duplicar a gravação de opt-out: o caminho existente de Campanhas valida
evento/ASR e atualiza a permissão compartilhada. Depois de changed e nos hooks
de grupos/permissões, chamar invalidate_schedule_contacts.

Permit do efeito: schedule_id, run_id, recipient_id, part_index e run_version
(hash SQL), além dos campos comuns de device/generation/settings_revision,
effect_id/owner/fence/expiração já usados pelo broker.

## Validação exigida

Dados/provedores sintéticos. Unitários de limites, fuso/DST, personalização,
recorrência, pureza dos GETs, proprietário antes de decodificar, idempotência e
cancelamento; PostgreSQL real com RLS/grants/concorrência e catálogo; ponte com
recibos/perda de resposta/revogação; DOM e Chrome/WebKit com os componentes reais.
Registros financeiros, mensagens, agendamentos e destinatários reais não fazem
parte dos testes. Falhas anteriores e limites da referência permanecem explícitos.

## Catálogo existente de clientes e papel do banco

As transações autenticadas do módulo usam `app_support`. A migração concede
somente SELECT em `customers.id`, `tenant_id`, `whatsapp_e164`, `name`, `active`,
`deleted_at` e `version`. A política `customers_subscription_schedule_support`
restringe essa leitura ao nicho `servico_assinaturas` persistido em
`business_onboarding`; as consultas continuam vinculadas explicitamente à
empresa autorizada. O papel não recebe SELECT da tabela inteira, acesso a
notas, escrita ou opção de conceder essas permissões a outros papéis. A
barreira de inicialização verifica os grants e a política; os testes reais
comparam uma empresa de assinaturas com uma empresa padrão.
