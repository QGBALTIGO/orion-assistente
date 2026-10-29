# Comunicação de agendamentos — 29/10/2026

Base auditada: `76e02ed9b650d8fc84dbb2e272312c78149ef832`.

## Atualização de publicação — piloto do administrador

O piloto foi publicado na aplicação existente, preservando contas, agendamentos, sessão e volume do WhatsApp. A API e o worker no commit `73c9904f1c38a4703d8b49ec20172f4aa00d7376` chegaram a `SUCCESS` em 29/10/2026 às 17h22 (America/Campo_Grande). O bridge permaneceu no deploy `152facbb-1cb0-4cf6-b7c8-01c88a9d5c3d`, com a mesma sessão e volume `/data`. PostgreSQL e Redis permaneceram online.

A migração aditiva chegou a `0049_preserved_phone_guard`, após backup privado confirmado pelo marcador `orion_appointment_0049_backup_schema_verified`. Foram preservados os guards normais; a exceção para o PN antigo do próprio aparelho exige a prova HMAC da sessão, propriedade atual, tenant, destinatário autorizado, lease e fence.

O piloto continua limitado à empresa do admin e aos números de teste explicitamente autorizados. A identidade do número conectado é independente da autorização do telefone que solicita a confirmação. Protocolos só liberam detalhes quando o remetente verificado corresponde ao cliente atual do agendamento. Uma tentativa real com números diferentes foi recusada; uma nova solicitação válida recebeu confirmação automática com recibo **entregue às 17h23**. Não houve troca de dispositivo ou migração para Meta.

Outras verificações reais no admin: reserva pública persistida e protocolo copiado; `wa.me` para o número conectado; aviso ao profissional sintético aceito; remarcações pela equipe e pelo cliente; versões antigas de lembretes canceladas; um lembrete válido aceito às 17h00; bloqueio de alterações após o limite; conclusão explícita do atendimento sintético e convite de avaliação aceito após o intervalo. Aceite do transporte continua diferente de entrega ou leitura. Profissionais sem telefone produziram falha controlada, sem fallback para o proprietário.

A recuperação administrativa de confirmação usa uma mensagem realmente recebida nas últimas 24h, o telefone cadastrado, a propriedade do canal e uma operação única na Central. Não aceita telefone, texto de resposta ou URL do navegador. O contato da página individual consulta o canal atual verificado, com limite de dois segundos, sem aproveitar telefones visuais do site.

A rodada [40956418796](https://github.com/QGBALTIGO/orion-assistente/actions/runs/40956418796) aprovou **195 testes focados**, **42 regressões PostgreSQL**, **436 testes de transporte QR**, **três lotes de concorrência** e **24 contextos de interface** (seis dimensões, dois temas, Chromium/WebKit). A suíte unitária ampla da mesma implementação teve **5.061 aprovados, 62 falhas e um skip**: as 62 falhas são as mesmas da base auditada, sem falhas novas nessa comparação. A recuperação foi qualificada com conta nativa e um canal preservado com credencial criptografada e registro canônico reais no PostgreSQL de teste.

A auditoria final acrescenta revalidação da credencial quando um segundo link de agendamento muda o fragmento na mesma aba: a página anterior é ocultada e o novo token é autenticado em uma página nova. A matriz visual inclui ida e volta entre dois tokens, sem manter dados ou ações do atendimento anterior.

PRs de publicação e correções: [171](https://github.com/QGBALTIGO/orion-assistente/pull/171), [172](https://github.com/QGBALTIGO/orion-assistente/pull/172), [173](https://github.com/QGBALTIGO/orion-assistente/pull/173), [174](https://github.com/QGBALTIGO/orion-assistente/pull/174) e [175](https://github.com/QGBALTIGO/orion-assistente/pull/175). A primeira tentativa de publicar 0049 foi bloqueada pelo reconhecimento incompleto da revisão anterior no predeploy; a versão anterior continuou online, o entrypoint foi corrigido e passou a ser exercitado contra PostgreSQL real. Falhas de fixtures detectadas nas rodadas intermediárias foram corrigidas sem reduzir os guards de autorização.

**A liberação geral e o escopo integral do pedido não estão declarados concluídos.** As lacunas funcionais e de qualificação do item 9 continuam registradas. O teste restrito do admin não comprova capacidade de mil usuários simultâneos, suporte oficial Meta, todos os papéis granulares de profissionais ou execução em celulares físicos. Rollback preferencial: desativar a nova comunicação na API e no worker, preservando tabelas, histórico e sessão; uma versão anterior do binário pode recusar a revisão mais nova no predeploy, exigindo correção compatível em vez de downgrade destrutivo.

## Inventário anterior às alterações

| Fluxo | Código real | Contrato a preservar |
|---|---|---|
| Reserva pública | `app/public_site_api.py:public_book` | cotação assinada, hold e confirmação persistidos antes da resposta |
| Agenda | `app/booking_service.py` | advisory lock por profissional, exclusão PostgreSQL de intervalos, snapshots e CAS |
| Identificação | `appointments.protocol`, `customers.whatsapp_e164`, IDs UUID | protocolo AG aleatório é identificador, nunca autorização |
| Profissionais | `professionals.phone_e164`, `professional_services` | vínculo e contato por tenant; nenhuma cópia automática ao dono |
| Cancelar/remarcar | `cancel_appointment`, `reschedule_appointment` | transação, histórico, idempotência e outbox Google |
| Conclusão | `management_models.transition_appointment` | finanças, comissões e fidelidade dependem da transição real |
| Conclusão por tempo | `auto_completion_worker.py` | recurso anterior opt-in conflita com este pedido; conclusão real deve prevalecer no novo fluxo |
| Canais QR | `qr_channels.py`, `qr_channel_scope.py`, bridge | credenciais por canal, leases, allowlist, sessão em volume `/data` |
| Ingresso | `qr_ai.run_locked`, `worker._event_answer` | deduplicação, guardas autenticadas e invalidação de propostas |
| Automação | `automation_runtime.py`, `automation_transport.py` | persistência PostgreSQL, consentimentos, resultado incerto sem reenvio cego |
| Papéis | `tenant_access.py`, `catalog_api.py` | owner/editor/viewer, admin/support; não há login individual vinculado a professional_id |
| Fusos | `tenants.timezone`, ZoneInfo, timestamptz | agenda em instantes UTC, exibição em IANA da empresa |
| Site | `management_site_preferences.public_slug`, `public_origins.py` | origem configurada no servidor; Host enviado não concede confiança |
| Serviços e disponibilidade | `load_bundle`, `check_slot`, `list_slots`, `appointment_items` | duração/preço em snapshots; jornadas, pausas, bloqueios e ausências do motor existente |
| Estados | `appointments.status`, `management_models.transition_appointment` | `hold`, `confirmed`, `completed`, `cancelled`, `no_show`; proposta guarda estado separado sem rebatizar registros antigos |
| Auditoria e recibos | `appointment_history`, `tenant_audit`, `calendar_outbox`, `automation_jobs` | ator e operação persistidos; entrega não inferida de aceite HTTP |

## Estado inicial Railway

Projeto `55750961-99ca-495e-b55e-4c07d78e53df`, ambiente `162d0407-3d4f-4163-9cb2-0c90154c707f`.
API, worker, QR, Postgres e Redis online; sem mudanças de configuração no inventário.
Somente ambiente production existente. Testes sintéticos devem usar banco isolado.
Volume QR `96a57936-6572-4604-887b-a7c8123263ff` deve ser preservado.

## Regressão antes de avançar

- `tests/integration/test_booking.py`: agenda, conflitos, idempotência e concorrência.
- `tests/integration/test_public_catalog.py`: criação pública e isolamento.
- `tests/integration/test_appointment_lifecycle.py`: cancelamento/remarcação e auditoria.
- `tests/integration/test_automation_postgres.py`: persistência e consentimentos.
- `tests/integration/test_qr_single_connection_postgres.py`: sessão/canal canônico.
- `tests/integration/test_rls.py`: isolamento real PostgreSQL.
- `tests/unit/test_appointment_lifecycle_unit.py`, UI pública e testes do bridge.

## Sequência de integração

1. Tornar a autorização e o limite de uma hora parte do motor atual; preservar os protocolos criptograficamente aleatórios existentes como identificadores sem poder de autenticação.
2. Acrescentar credenciais independentes com hash, revogação e validade; gestão pública sem informação no GET de prévia.
3. Integrar resposta segura ao protocolo e ação WhatsApp após persistência.
4. Acrescentar eventos transacionais e processamento persistente reutilizando as credenciais/recibos do transporte existente.
5. Integrar avaliação, configurações e acompanhamento nos componentes do Orion.
6. Executar regressões PostgreSQL/Redis, matriz de interface, concorrência e revisão de segurança antes de qualquer publicação.

O transporte não será ampliado além das permissões reais do canal. Testes reais de WhatsApp dependem de números explicitamente autorizados. Nenhum envio a cliente real é autorizado por este documento.


## Relatório de implementação e homologação

Registro inicial de homologação, anterior à liberação do piloto documentada no topo desta página.

**Registro histórico da rodada inicial.** O estado operacional, a publicação e os testes reais posteriores de WhatsApp estão descritos na atualização do topo. As limitações do lançamento geral permanecem discriminadas abaixo.

### 1. Funcionalidades implementadas

- A criação pública continua usando cotação, hold, confirmação e recibo de idempotência existentes. A resposta só acrescenta a ação `wa.me` depois dos commits. Falhar ao consultar o canal não desfaz uma reserva já salva. Há cópia de protocolo como alternativa.
- A detecção do protocolo integra os ingressos WhatsApp existentes. Exige a empresa do canal e o telefone verificado do remetente correspondente ao cadastro atual. Protocolo de outro contato/empresa retorna uma resposta genérica sem dados. Mensagens repetidas usam recibo criptografado e versão do agendamento.
- A resposta pública anônima **não entrega um link de gestão**: conhecer/preencher o telefone de outra pessoa no formulário público não prova sua identidade. O link só é emitido ao contato verificado pelo canal, ou por uma comunicação autorizada destinada a esse contato.
- Página individual com identidade da empresa, dados atuais, cancelamento com confirmação, horários reais, remarcação, prazo no servidor, proposta de alteração com aceite/recusa, estado concluído, avaliação privada e novo agendamento com pré-seleção segura de serviço/profissional.
- Eventos de confirmação notificam o profissional atribuído independentemente do envio do protocolo pelo cliente. Não há fallback para telefone do proprietário. Cancelamentos e remarcações enfileiram os avisos cruzados.
- Outbox persistente no PostgreSQL, versão do agendamento, chave de idempotência, invalidação de lembretes antigos e processamento pelo worker Arq existente. Redis não é a única fonte das intenções de envio.
- Lembrete programado para início menos 60 minutos, com descarte após dois minutos de atraso. A reserva feita depois do instante previsto não recebe uma mensagem atrasada afirmando faltar uma hora.
- Conclusão efetiva utiliza a transição já existente no painel. Com a flag nova ativa, a conclusão automática somente pela passagem do tempo fica desabilitada. Avaliações exigem histórico de conclusão por papel autorizado da equipe.
- Configurações no painel existente, acompanhamento limitado aos últimos cem envios, avaliações privadas e intervalos de retorno configurados por serviço. Nenhuma frequência clínica é inferida. Um agendamento futuro já existente suprime o incentivo duplicado.

### 2. Arquivos alterados

A lista abaixo corresponde ao diff da base auditada até a implementação desta branch. As mudanças de cobrança/predeploy apenas mantêm o reconhecimento das revisões aditivas e a barreira de migração; não reformulam o produto de cobrança.

```text
.github/workflows/appointment-communications.yml
alembic/versions/0047_appointment_access.py
alembic/versions/0048_appointment_notifications.py
app/appointment_access.py
app/appointment_access_api.py
app/appointment_changes.py
app/appointment_communications_api.py
app/appointment_confirmation.py
app/appointment_errors.py
app/appointment_lifecycle.py
app/appointment_messages.py
app/appointment_notifications.py
app/auto_completion_worker.py
app/booking_api.py
app/booking_service.py
app/database.py
app/main.py
app/public_site_api.py
app/qr_ai.py
app/static/orion-appointment-communications.js
app/static/orion-appointment.css
app/static/orion-appointment.html
app/static/orion-appointment.js
app/static/orion-public.js
app/static/orion-suite.css
app/static/orion-suite.js
app/static/panel.html
app/whatsapp_bot.py
app/worker.py
docs/appointment-communications-implementation.md
scripts/backup_and_migrate_appointment_communications.py
scripts/backup_and_migrate_cakto.py
scripts/backup_and_migrate_direct_checkout.py
scripts/backup_and_migrate_plans.py
scripts/backup_and_migrate_registration.py
scripts/start_api.py
tests/e2e/appointment_communications.py
tests/integration/test_appointment_access_pg.py
tests/integration/test_appointment_load_pg.py
tests/integration/test_appointment_migration_pg.py
tests/integration/test_appointment_notifications_pg.py
tests/integration/test_booking.py
tests/unit/test_appointment_access.py
tests/unit/test_appointment_messages.py
tests/unit/test_customer_deadline.py
```

### 3. Migrações

| Revisão | Mudança | Situação |
|---|---|---|
| `0047_appointment_access` | hashes de capacidades, revogação, bloqueio de emissão por agendamento, avaliações privadas e RLS | aplicada apenas em PostgreSQL efêmero de CI |
| `0048_appointment_notifications` | configurações, outbox, recibos criptografados, propostas de remarcação, intervalos por serviço e scanner restrito | aplicada apenas em PostgreSQL efêmero de CI |

As migrações são aditivas. Não reescrevem protocolos, sessões QR nem agendamentos antigos. Novas configurações começam desativadas. O downgrade recusa exclusão destrutiva do histórico; o rollback operacional previsto desativa as flags e mantém os dados. O script `backup_and_migrate_appointment_communications.py` exige a revisão de origem `0046_orion_direct_checkout`, backup privado confirmado, lock de publicação e execução restrita ao migrator antes da atualização. Não foi executado na produção.

### 4. Regras de negócio

- Limite inclusivo: a operação do cliente deve confirmar com relógio do PostgreSQL até `start_at - 60 minutos`; após esse instante é recusada. Há revalidação no comando transacional, incluindo a condição do UPDATE. Holds temporários conservam sua regra anterior.
- Protocolo existente `AG-` com seis caracteres aleatórios permanece um identificador. A autorização usa uma credencial distinta com segredo de 256 bits, empresa, cliente e agendamento vinculados.
- Reservas, cancelamentos, remarcações e conclusão continuam no motor da agenda. Locks por profissional, restrição de exclusão PostgreSQL, controle de versão, snapshots e recibos anteriores são preservados.
- Na política de aceite, o horário original permanece reservado. A proposta expira em até 24 horas, antes do limite do horário original. O novo horário não é prometido nem reservado: sua disponibilidade é revalidada no aceite transacional; conflito preserva a reserva original.
- Avaliação: uma por atendimento, de uma a cinco estrelas, comentário opcional de até mil caracteres, sem edição nesta versão, privada ao negócio. Estado `completed` sem histórico de conclusão autorizado não habilita avaliação.
- Templates são centralizados, com dados do banco, campos opcionais omitidos, data brasileira, relógio de 24 horas e IANA da empresa. O limite atual do transporte é respeitado; falta de dados resulta em falha controlada.
- Os avisos proativos a clientes exigem preferência operacional existente. Convites de avaliação/retorno respeitam também a preferência de marketing. Isso implementa controles de comunicação; não constitui certificação de conformidade jurídica.
- Resultado incerto não é reenviado automaticamente. Uma inspeção sem recibo válido continua incerta e avança seu horário de inspeção para não monopolizar o scanner. `accepted`, `delivered` e `read` têm significados diferentes.

### 5. Proteções de segurança

- Todas as tabelas novas têm FORCE RLS, chaves com empresa e grants restritos. O scanner SECURITY DEFINER devolve somente IDs de empresa/job em lote limitado; não abre conteúdo privado ao suporte.
- Apenas hash SHA-256 da capacidade fica na tabela de acesso. Segredo no fragmento `/a#...`, enviado em corpo JSON para o servidor, sem token em caminhos HTTP, query string ou analytics. Mensagens que precisam conservar o link usam a criptografia Fernet já adotada no Orion.
- Página sem terceiros, CSP restritiva, `no-store`, `no-referrer`, `noindex`. Prévia GET não apresenta dados. Campos e comentários são renderizados como texto; imagens da marca precisam pertencer ao mesmo tenant e ao armazenamento local permitido.
- Acesso revalidado em transações do motor, revogação persistente inclusive para novas emissões e invalidação se a versão do cadastro do cliente mudar. Rate limiting usa Redis e falha de forma fechada.
- Same-origin nas ações públicas; autenticação e permissões existentes no painel; IDs/telefone recebidos do navegador não concedem poderes de administração.
- Segredos não foram adicionados ao GitHub. Erros de banco nos endpoints sensíveis são sanitizados; diagnósticos não retornam token, corpo criptografado nem credenciais.
- Não se acrescentou webhook desprotegido. Os ingressos existentes mantêm seus controles de mensagens privadas, identidade PN/LID, replay, canal e autoenvio. A matriz completa desses controles no provedor real permanece pendente.
- A revisão manual corrigiu a emissão indevida de capacidade na resposta anônima e a possibilidade de emissão após revogação. O serviço formal Codex Security não estava disponível nesta sessão; não existe resultado de scan formal a declarar.

### 6–7. Testes executados e aprovados

A execução [40938250622](https://github.com/QGBALTIGO/orion-assistente/actions/runs/40938250622), commit `d21c9a3aa295951e980e777c15019f1042963d53`, terminou em **SUCCESS** às 19:20:59 UTC de 29/10/2026.

| Qualificação | Resultado | Limite da evidência |
|---|---:|---|
| Unitários focados + PostgreSQL/RLS/API/migração | 98 aprovados | inclui 22 unitários; banco PostgreSQL 16 real, papéis separados e Redis 7 no runner |
| Site público, automações existentes e conexão QR canônica | 42 aprovados | banco real; transportes externos de teste, sem entrega WhatsApp |
| Concorrência progressiva 100/500/1.000 tentativas | 3 aprovados | máximo de 50 em voo, motor do banco isolado |
| Interface Chromium + WebKit | 24 contextos aprovados | seis tamanhos × dois motores × claro/escuro; APIs sintéticas de UI |
| Capturas de interface | 72 PNGs | confirmação, conclusão e configurações; amostras revisadas visualmente |
| Migração populada 0046 → 0048 + restore | aprovada dentro dos 98 testes | 95 tabelas/99 registros anteriores preservados, quatro registros QR sintéticos desativados, zero sessões reais |

A migração compara hashes de todas as colunas preexistentes e grants, confere defaults das novas colunas, ausência de ativação automática e um INSERT do contrato runtime anterior sob RLS. O backup cifrado sintético tem 730.296 bytes, SHA-256 `ea0e1c712a3e39d1b06e79b040317e8e3b46824f37d6bbac8bdb925eea8afcf3`; sua leitura/decifragem e a restauração real foram validadas. Não foi executado o binário antigo da API nem restaurado backup de produção.

Os testes focados produziram seis avisos de depreciação; não foram falhas. O workflow também informou depreciação de Node 20 em ações existentes. Outros workflows gerais disparados no PR podem ter resultados independentes; esta tabela não declara todos os checks do repositório aprovados.

Na comparação completa de testes unitários, executada localmente no mesmo ambiente:

| Versão | Total | Aprovados | Falhas | Ignorados |
|---|---:|---:|---:|---:|
| Base `76e02ed9` | 5.087 | 5.024 | 62 | 1 |
| Código `63f6645c` | 5.109 | 5.046 | 62 | 1 |

O conjunto de identificadores dos 62 testes com falha é exatamente igual nas duas execuções. Isso é evidência de ausência de novas falhas nessa suíte, **não de que a suíte inteira esteja aprovada**. A alteração posterior `841c58e` foi qualificada na suíte focada de PostgreSQL/interface; a suíte ampla acima não foi repetida depois dela.

### 8. Falhas encontradas e correções

| Falha durante a implementação | Correção | Evidência |
|---|---|---|
| Aceite de proposta usava UTC na validação que exigia offset da empresa | converter o instante persistido para ZoneInfo da empresa antes do motor de disponibilidade | teste de proposta com horário da empresa aprovado após `160cf95` |
| Cleanup da fixture removia registros antes das propostas com FK | corrigir a ordem de limpeza do banco sintético | CI posterior aprovado |
| Novo migrator era chamado mesmo com flag de migração desligada | chamar somente com `ORION_RUN_APPOINTMENT_COMMUNICATION_MIGRATION=1` | teste de predeploy voltou a passar; suíte ampla voltou às mesmas 62 falhas da base |
| Configuração não seguia todos os componentes visuais do painel | reutilizar `os-field`, `os-button`, `os-toggle` e espaçamentos existentes | capturas Chromium/WebKit e revisão visual |
| Inputs excediam a largura interna do cartão | box sizing restrito ao módulo e teste de contenção de cada input | matriz de interface da revisão final |
| Recibo sem aceite não avançava o horário de inspeção | atualizar apenas o horário, mantendo estado desconhecido sem reenviar | teste PostgreSQL dedicado |
| Teste histórico de 0019 até head exigia tabela `plans` vazia apesar do seed de três planos existente em 0045 | manter o teste antigo sem alterações; criar teste dedicado à origem prevista de publicação 0046, verificando todos os dados/colunas/grants anteriores e restore | resultado histórico registrado no [run 40937247955](https://github.com/QGBALTIGO/orion-assistente/actions/runs/40937247955); nova qualificação discriminada na CI final |

As 62 falhas herdadas concentram-se em contratos antigos de UI/login/predeploy, doubles Redis incompatíveis com exclusão de múltiplas chaves, harness Node sem `AbortController`, fixture de agendamento sem `price_mode` e URL de migração não configurada no teste unitário. Também há testes de escopo e autenticação entre essas falhas. A reprodução idêntica na base não prova segurança dessas áreas; requer triagem separada, preservando o escopo solicitado. O risco permanece aberto: as falhas de escopo/login e de predeploy devem ser avaliadas antes de liberar produção; não são classificadas como inofensivas por serem antigas. Não foram apagados testes nem corrigidas asserções só para obter verde.

<details>
<summary>Identificadores das 62 falhas reproduzidas na base e na branch</summary>

```text
tests.unit.test_auth_ui::test_member_logs_directly_into_normal_dashboard_without_admin_claim
tests.unit.test_auth_ui::test_orion_theme_ui_is_accessible_and_mobile_usable
tests.unit.test_legacy_admin_identity::test_native_account_cannot_be_used_through_the_generic_password_session
tests.unit.test_legacy_admin_identity::test_native_sessions_preserve_csrf_origin_and_logout
tests.unit.test_onboarding_goals::test_quick_release_is_in_existing_predeploy_chain_and_rechecks_current_schema
tests.unit.test_onboarding_state_encoding::test_actual_state_values_fingerprints_snapshot_and_http_bytes_are_identical[1000-False]
tests.unit.test_onboarding_state_encoding::test_actual_state_values_fingerprints_snapshot_and_http_bytes_are_identical[113-False]
tests.unit.test_onboarding_state_encoding::test_actual_state_values_fingerprints_snapshot_and_http_bytes_are_identical[200-False]
tests.unit.test_onboarding_state_encoding::test_actual_state_values_fingerprints_snapshot_and_http_bytes_are_identical[281-False]
tests.unit.test_one_time_migration::test_database_account_schema_matches_restricted_migration
tests.unit.test_panel_fulfillment::test_panel_fulfillment_controller[candidate-contract]
tests.unit.test_panel_fulfillment::test_panel_fulfillment_controller[candidate-version-drift]
tests.unit.test_panel_fulfillment::test_panel_fulfillment_controller[compile]
tests.unit.test_panel_fulfillment::test_panel_fulfillment_controller[confirm-version-drift]
tests.unit.test_panel_fulfillment::test_panel_fulfillment_controller[delivery]
tests.unit.test_panel_fulfillment::test_panel_fulfillment_controller[disabled-legacy]
tests.unit.test_panel_fulfillment::test_panel_fulfillment_controller[empty-and-network]
tests.unit.test_panel_fulfillment::test_panel_fulfillment_controller[history-current-separate]
tests.unit.test_panel_fulfillment::test_panel_fulfillment_controller[history-late-response]
tests.unit.test_panel_fulfillment::test_panel_fulfillment_controller[history-unavailable]
tests.unit.test_panel_fulfillment::test_panel_fulfillment_controller[late-logout]
tests.unit.test_panel_fulfillment::test_panel_fulfillment_controller[late-mode]
tests.unit.test_panel_fulfillment::test_panel_fulfillment_controller[late-phone]
tests.unit.test_panel_fulfillment::test_panel_fulfillment_controller[late-profile]
tests.unit.test_panel_fulfillment::test_panel_fulfillment_controller[late-quote]
tests.unit.test_panel_fulfillment::test_panel_fulfillment_controller[late-tenant]
tests.unit.test_panel_fulfillment::test_panel_fulfillment_controller[optional-no-appointment]
tests.unit.test_panel_fulfillment::test_panel_fulfillment_controller[pickup-confirm]
tests.unit.test_panel_fulfillment::test_panel_fulfillment_controller[preflight-late-error]
tests.unit.test_panel_fulfillment::test_panel_fulfillment_controller[profile]
tests.unit.test_panel_fulfillment::test_panel_fulfillment_controller[quote-preflight-offline]
tests.unit.test_panel_fulfillment::test_panel_fulfillment_controller[quote-version-drift]
tests.unit.test_panel_fulfillment::test_panel_fulfillment_controller[selection-invalidates]
tests.unit.test_panel_fulfillment::test_panel_fulfillment_controller[unknown-or-stale-candidate]
tests.unit.test_panel_session::test_panel_login_cookie_csrf_and_bridge
tests.unit.test_panel_session::test_panel_ui_includes_qr_pairing_chats_without_manual_support_token
tests.unit.test_panel_session_pool::test_pool_survives_requests_but_logout_and_csrf_remain_live
tests.unit.test_panel_session_pool::test_shared_pool_does_not_keep_deleted_claims_or_survive_redis_outage_as_auth
tests.unit.test_panel_simulator_feedback::test_panel_simulator_feedback_controller[audio]
tests.unit.test_panel_simulator_feedback::test_panel_simulator_feedback_controller[clear-conversation]
tests.unit.test_panel_simulator_feedback::test_panel_simulator_feedback_controller[clear-logout]
tests.unit.test_panel_simulator_feedback::test_panel_simulator_feedback_controller[clear-style]
tests.unit.test_panel_simulator_feedback::test_panel_simulator_feedback_controller[clear-tenant]
tests.unit.test_panel_simulator_feedback::test_panel_simulator_feedback_controller[clear-version]
tests.unit.test_panel_simulator_feedback::test_panel_simulator_feedback_controller[download-failure]
tests.unit.test_panel_simulator_feedback::test_panel_simulator_feedback_controller[export]
tests.unit.test_panel_simulator_feedback::test_panel_simulator_feedback_controller[ratings]
tests.unit.test_panel_simulator_feedback::test_panel_simulator_feedback_controller[scope-drift]
tests.unit.test_preview_audio::test_cookie_auth_keeps_account_identity_and_requires_csrf_before_preview
tests.unit.test_qr_followup_context::test_done_with_four_bookings_uses_fresh_receipt_only_as_scoped_reschedule_reference
tests.unit.test_qr_multi_flow::test_cancel_then_final_reschedule_prepares_summary_without_cancelling_or_moving
tests.unit.test_qr_multi_flow::test_choosing_one_of_multiple_appointments_keeps_the_date_and_clock_already_supplied
tests.unit.test_qr_multi_flow::test_reschedule_conflict_preserves_original_and_only_new_message_reads_other_slots
tests.unit.test_scoped_panel_account::test_live_scope_lookup_keeps_revocation_tenant_and_account_separation
tests.unit.test_single_whatsapp_release::test_all_reviewed_predeploy_origins_reach_single_connection_in_order[0038_email_outbox-expected0]
tests.unit.test_single_whatsapp_release::test_all_reviewed_predeploy_origins_reach_single_connection_in_order[0039_registration_confirmation-expected1]
tests.unit.test_single_whatsapp_release::test_all_reviewed_predeploy_origins_reach_single_connection_in_order[0040_professional_phone-expected2]
tests.unit.test_single_whatsapp_release::test_all_reviewed_predeploy_origins_reach_single_connection_in_order[0041_quick_onboarding_activation-expected3]
tests.unit.test_single_whatsapp_release::test_all_reviewed_predeploy_origins_reach_single_connection_in_order[0042_admin_account_controls-expected4]
tests.unit.test_single_whatsapp_release::test_all_reviewed_predeploy_origins_reach_single_connection_in_order[0043_cakto_events-expected5]
tests.unit.test_single_whatsapp_release::test_all_reviewed_predeploy_origins_reach_single_connection_in_order[0044_single_whatsapp_connection-expected6]
tests.unit.test_single_whatsapp_release::test_all_reviewed_predeploy_origins_reach_single_connection_in_order[0045_plan_trials_checkout-expected7]
```

</details>

### 9. Testes e partes do escopo ainda pendentes

- Homologação real do WhatsApp: recebimento, sessão persistente após reinício, resolução PN/LID, reconexão, envio, entrega e leitura com números expressamente autorizados. Os emissores de teste em Python são spies; não são evidência de mensagens reais.
- Ensaios operacionais com worker/Redis/rede reiniciados durante um envio real e observação de recibos do provedor. Testes atuais validam estados persistidos e falhas simuladas, sem demonstrar todos esses acidentes reais.
- Dispositivos Android/iOS físicos, abertura `wa.me` em aplicativos móveis e comportamento de WhatsApp Web. A matriz atual usa Chromium/WebKit em viewports equivalentes.
- Carga com 100, 500 e mil usuários simultâneos de ponta a ponta e métricas da infraestrutura (CPU PostgreSQL, conexões, saturação Redis e atraso do transporte). O ensaio abaixo é limitado ao motor do banco e máximo de cinquenta operações em voo.
- Pentest completo de webhooks, assinatura inválida, SQL injection e força bruta em escala. Os testes executados cobrem autorização, origem, validação, RLS, revogação, concorrência, idempotência e comentários maliciosos, sem substituir essa qualificação integral.
- Login individual de profissional vinculado a `professional_id` e permissões configuráveis por profissional: o modelo atual só fornece papéis de equipe por empresa. Os links de profissional usam o painel autenticado existente; não dão acesso privilegiado por URL. Esse novo vínculo granular não foi implementado.
- Troca de profissional de um agendamento, avisos a antigo/novo responsável, múltiplas unidades e recursos compartilhados não são oferecidos pelo motor de remarcação atual e não foram acrescentados nesta implementação. Os testes comprovam a atribuição existente por ID, não esses fluxos ausentes.
- Verificação OTP para um remetente diferente do cadastro não foi implantada; não havia canal de autenticação de cliente aprovado disponível. O acesso permanece negado, com orientação para utilizar o contato cadastrado ou atendimento da empresa.
- Domínio personalizado por empresa não foi configurado: não foi encontrado um cadastro confiável de domínios hospedados. Links usam a origem canônica configurada, sem confiar no Host da requisição.
- Não há autenticação de cliente no site público para reaproveitamento de nome/telefone em nova reserva. Serviço/profissional são pré-selecionados sem PII na URL e podem ser alterados.
- A retenção técnica de payloads foi implementada com limpeza em lotes após 90 dias, preservando o histórico de agendamentos e a idempotência. A revisão jurídica específica das finalidades e a qualificação integral da política de retenção permanecem pendentes.
- Migração, backup e restauração da produção, rollout, smoke tests autenticados após deploy e piloto real permanecem pendentes. Nenhuma validação de CI é apresentada como resultado em produção.

### 10. Concorrência

Valores observados na execução final acima:

| Tentativas | Máximo em voo | Duração (s) | Operações/s | p50 (ms) | p95 (ms) | p99 (ms) | Confirmadas / conflitos 409 | CPU Python (s) | Pico RSS Python (KiB) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 | 50 | 1,624 | 61,59 | 723,21 | 1.172,56 | 1.288,71 | 1 / 99 | 0,779 | 164.232 |
| 500 | 50 | 3,811 | 131,19 | 302,62 | 928,29 | 1.173,35 | 1 / 499 | 2,469 | 164.232 |
| 1.000 | 50 | 6,255 | 159,86 | 246,19 | 703,96 | 1.078,80 | 1 / 999 | 4,399 | 164.232 |

O cenário disputa **um mesmo horário** no motor real com dados sintéticos; em cada lote exatamente uma reserva foi confirmada e as demais retornaram conflito 409 esperado, sem duplicidade. O limite foi de cinquenta operações simultâneas, inclusive no lote de mil tentativas. A memória e CPU medidas são do processo Python do teste, não do banco nem da produção. Esses números não sustentam uma promessa de capacidade comercial do Orion.

### 11. WhatsApp real

| Verificação | Resultado nesta entrega |
|---|---|
| Payload e composição dos textos | testes automatizados aprovados |
| Escolha de destinatário por empresa/profissional | testes com banco real e sender sintético |
| Protocolo correspondente ao contato | teste com banco real, adaptador de ingresso |
| Provedor aceitou uma mensagem real nova | não executado |
| Mensagem nova entregue/lida | não executado |
| Reconexão e persistência da sessão real | não executado; volume e sessão não manipulados |
| Botão nativo WhatsApp | não implementado nem alegado; somente texto e link |

Não se utilizou número de Peixoto, Guilherme ou cliente real para inferir autorização. São necessários uma empresa/canal de homologação e pelo menos dois contatos de teste explicitamente autorizados, representando cliente e profissional.

### 12. Limitações do transporte

O envio novo usa exclusivamente o transporte QR gerenciado existente, com credenciais por canal, allowlist de até dez contatos e recibos por operação. O módulo recusa destinatário não permitido e registra `transport_recipient_not_authorized`; não muda limites nem política do bridge. Isso impede um lançamento geral para clientes arbitrários no estado atual.

A conexão da Meta preparada no projeto não foi ativada, migrada ou tratada como equivalente ao QR. Não foram comprovados elegibilidade, templates aprovados, janela de atendimento, consentimentos específicos, coexistência, botões oficiais ou limites da Cloud API. Não existe fallback que contorne restrições do provedor. A conectividade de um serviço Railway não prova que uma sessão WhatsApp esteja pronta ou que uma mensagem tenha sido entregue.

### 13. PR e commits

- [PR #171 em rascunho](https://github.com/QGBALTIGO/orion-assistente/pull/171), branch `codex/appointment-communications-20261029`.
- Base auditada: `76e02ed9b650d8fc84dbb2e272312c78149ef832`.
- Commits incrementais: `09ad297` (prazo/auditoria), `17c65da` (links), `f273781` (fluxo integrado), `5a8b80b` (UI/carga/rollout), `208bb5f`/`160cf95` (aceite e correção de fuso), `c10dc1d` (revogação/diagnóstico/retorno), `4763e70` (compatibilidade predeploy/marca), `63f6645` (padrão visual), `841c58e` (contenção e inspeção justa), `45593e7`/`e9f1ed7` (regressões adicionais), `d21c9a3` (preservação da origem real 0046).
- Commit com código funcional mais recente: `841c58edc3cb24bc7a7b2b8e02a62229d12dd8e2`; commits posteriores citados acima alteram somente o workflow e os testes. O relatório pode receber commit documental posterior sem mudar esse código.
- Não houve merge na main nem acionamento de publicação Railway desta branch.

### 14–15. Serviços publicados e estado final do Railway

Consulta somente de leitura ao ambiente production em 29/10/2026: cinco serviços online, nenhuma issue ativa e nenhuma falha recente na janela consultada. **São os deployments existentes, sem esta implementação.**

| Serviço | Deployment existente | Situação |
|---|---|---|
| orion-assistente | `b5d69a47-5d2a-49cd-b7cc-d8b949f6b7c1` | SUCCESS / 1 réplica online |
| orion-worker | `26bd5382-3cc9-4cd2-b1d9-d8d359223c35` | SUCCESS / 1 réplica online |
| orion-qr-bridge | `73ec28ea-c1ed-4382-8cf1-05e5163dd6b0` | SUCCESS / 1 réplica online |
| Redis | `b369ead8-39e4-4bd3-8c9f-3fb929494788` | SUCCESS / 1 réplica online |
| Postgres | `47bbb965-a91a-4bde-a809-d555d75bc627` | SUCCESS / 1 réplica online |

Volumes persistentes, credenciais, contas, configurações, históricos e sessões não foram modificados no ambiente live. Havia um patch staged `f4d43dd9-507a-48ff-9611-318179505fe7` sem mudanças, informado pelo Railway; não foi aplicado nem descartado.

### 16. Regressão de funcionalidades existentes após deploy

Não aplicável: nenhum deploy desta mudança ocorreu. Os testes isolados da agenda e as regressões discriminadas na CI são evidência prévia à publicação. Online/SUCCESS no Railway não substitui validação funcional autenticada nem comprova sessão WhatsApp conectada. As verificações de leitura em `/health`, `/panel` e `/s/barbearia-peixoto` retornaram HTTP 200; `/health` respondeu `{"status":"ok"}`. Não foram efetuados login nem agendamentos na produção.

### 17. Pendências para liberação

1. Definir contatos e empresa/canal de teste autorizados, sem enviar mensagens a clientes reais.
2. Resolver a restrição da allowlist para um lançamento geral por uma estratégia autorizada de transporte; não ampliar silenciosamente os poderes do QR nem migrar o número para Meta.
3. Implementar/qualificar os fluxos granulares de profissional e demais lacunas funcionais listadas no item 9 antes de declarar o escopo completo.
4. Concluir retenção, triagem das falhas herdadas com risco de autenticação/predeploy e qualificação operacional de rede/worker/recibos.
5. Publicar primeiro com flags desligadas, backup privado verificável, revisão de variáveis/volumes e confirmação do commit exato; habilitar piloto restrito só após homologação aprovada.
6. Verificar pós-deploy API, worker, bridge, banco, Redis, migrações, logs e fluxos anteriores. Recuar desativando as flags e preservando tabelas/sessão se houver regressão.

A flag global `APPOINTMENT_COMMUNICATIONS_ENABLED` deve estar coerente na API e no worker; a configuração por empresa também inicia desligada. A flag `ORION_RUN_APPOINTMENT_COMMUNICATION_MIGRATION` libera somente a migração no predeploy e é retirada do processo da API pelo launcher. Foi habilitada na publicação controlada do piloto e descartada do processo normal da API pelo launcher.

### 18. Evidências e reprodução

- [Código qualificado no commit d21c9a3](https://github.com/QGBALTIGO/orion-assistente/commit/d21c9a3aa295951e980e777c15019f1042963d53).
- [Execução final e logs completos](https://github.com/QGBALTIGO/orion-assistente/actions/runs/40938250622).
- [Artefato com 72 PNGs, resultados da UI, migração e três relatórios de carga](https://github.com/QGBALTIGO/orion-assistente/actions/runs/40938250622/artifacts/12926511695), ID `12926511695`, SHA-256 `6ce9e6ace6e951092de3c9a6396c82bed29795f79343ea592f9e08ecad502a3e`, expiração indicada pelo GitHub em 27/01/2027.
- [Execução anterior das regressões públicas e do mesmo código funcional](https://github.com/QGBALTIGO/orion-assistente/actions/runs/40937099280): 97 + 42 testes, três lotes de carga e 24 contextos UI aprovados.
- [Execução que revelou a expectativa histórica incompatível em plans](https://github.com/QGBALTIGO/orion-assistente/actions/runs/40937247955), mantida no registro e descrita no item 8.

Nenhuma dessas evidências contém confirmação de entrega real do WhatsApp. O artefato tem somente dados sintéticos de teste; não inclui dump da produção, credenciais nem arquivos da sessão QR.

Comandos principais do workflow:

```sh
python -m pip install -c requirements.lock '.[test]'
alembic upgrade head
pytest -q -ra tests/unit/test_customer_deadline.py tests/unit/test_appointment_access.py tests/unit/test_appointment_messages.py tests/integration/test_appointment_access_pg.py tests/integration/test_appointment_notifications_pg.py tests/integration/test_booking.py tests/integration/test_rls.py tests/integration/test_appointment_lifecycle.py tests/integration/test_appointment_migration_pg.py
pytest -q -ra tests/integration/test_public_catalog.py tests/integration/test_automation_postgres.py tests/integration/test_qr_single_connection_postgres.py
ORION_APPOINTMENT_LOAD=1 pytest -q -s tests/integration/test_appointment_load_pg.py
tests/integration/test_appointment_migration_pg.py
python tests/e2e/appointment_communications.py
```

A execução exige PostgreSQL 16 isolado com papéis runtime/migrator/support, Redis de teste e as variáveis usadas no workflow. Os valores de CI são descartáveis e não são credenciais da produção. O teste de migração usa somente bases aleatórias de loopback. A suíte ampla local foi executada com `pytest -q tests/unit --junitxml=...` na base e na branch, comparando os identificadores de falha.
