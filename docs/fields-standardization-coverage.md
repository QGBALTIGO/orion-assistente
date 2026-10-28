# Padronização dos campos — cobertura e evidências

Este documento acompanha a implementação solicitada em 28/10/2026. A base de comparação é `ba563ef86680fa47548a4255e91c931ac4ec8a04`, posterior ao levantamento original `6ef8307f69be35af79784a2c546290566839a45e`. O código em edição é a referência para a entrega; as contagens abaixo têm método reproduzível e não representam, sozinhas, aprovação visual ou funcional.

**Estado deste relatório: cobertura consolidada e evidências por revisão, em 28/10/2026.** A execução DOM agregada passou 722/722 testes; a seleção backend/assets passou 285 testes, incluindo requisições HTTP reais via ASGI. O CI confirmou esses mesmos totais na revisão `5379b8d0258623b9d416284a53af48bbfe9ab3f7`. As revisões CUA abaixo ocorreram em uma prévia isolada com dados sintéticos. O fechamento atualizado de Chromium/WebKit, commit integrado e revisão publicada é registrado no [PR #122](https://github.com/QGBALTIGO/orion-assistente/pull/122), para distinguir cada rodada da versão efetivamente entregue.

Não foi fornecida a planilha `Levantamento_Campos_Orion.xlsx`. Por isso, a referência de cada campo é **arquivo + formulário/escopo + `name`/`id`**, sem IDs `ORI-xxx` inventados. Os 34 grupos deste documento organizam a revisão atual; não estabelecem correspondência com linhas desconhecidas da planilha.

## Método e contagens

O indexador `scripts/inventory_fields.py` combina tags HTML, chamadas dos construtores da suíte/onboarding e metadados explícitos dos controles criados com DOM. Ele não importa a aplicação, não consulta APIs e não precisa de sessão, banco ou credenciais. A opção `--ref` lê arquivos de uma revisão Git; sem ela, lê a árvore de trabalho.

```bash
python scripts/inventory_fields.py --ref ba563ef86680fa47548a4255e91c931ac4ec8a04 --output /tmp/orion-fields-baseline.json
python scripts/inventory_fields.py --output /tmp/orion-fields-current.json
python scripts/inventory_fields.py --format markdown --output /tmp/orion-fields-current.md
```

Uma definição reutilizada em formulários distintos é contada em cada formulário: criação/edição do negócio no onboarding, serviço/produto e quatro editores de jornada/pausa são expandidos explicitamente. Uma família gerada por item — profissional, serviço, adicional, regra de mensagem ou dia da semana — aparece uma vez, independentemente da quantidade de registros. Botões de ação, opções de um `select`, entradas `hidden`, textos calculados e sugestões clicáveis não são campos preenchíveis adicionais.

| Classificação | Ocorrências | Tratamento |
| --- | ---: | --- |
| Ativas na suíte, acesso, onboarding e páginas públicas | 281 | Padronizar e validar conforme contrato |
| Legadas ainda acessíveis pelo painel | 97 | Preservar acesso e contrato; integrar os campos existentes |
| **Total preenchível ativo** | **378** | **34 grupos de revisão** |
| Dormentes, sem entrada ativa demonstrada | 36 | Preservar; não reativar navegação ou envio |
| Somente leitura | 2 | Preservar leitura e cópia; não normalizar conteúdo |
| Placeholders permanentemente desabilitados | 2 | Não ativar pagamentos ou voz |
| Segredo técnico QR | 1 | Manter proteção e conteúdo literal |
| Honeypot comercial | 1 | Manter oculto e sem máscara |
| **Total do índice, incluindo exclusões** | **420** | Não confundir com total de campos visíveis simultaneamente |

Na conferência da base `ba563ef` e da árvore em edição, os conjuntos de arquivo/formulário/localizador coincidiram. A troca de `number` por texto decimal ou de texto por seleção altera o tipo do controle, mas não cria outro campo de negócio. O total antigo de 428 não é um denominador intercambiável: houve remoções na Central e a contagem atual mantém famílias dinâmicas sem multiplicá-las pelos itens gerados. Não se deve concluir que a diferença numérica significa exatamente 50 campos removidos.

O indexador é uma evidência de descoberta e rastreabilidade. A análise estática não demonstra que um controle está visível em todos os perfis, que um recurso foi habilitado pelo servidor ou que sua geometria passou no navegador. Flags como `has_mask_optin` descrevem o ponto de chamada encontrado; um construtor pode acrescentar atributos durante a execução. A prova de comportamento vem dos testes de consumidor e das matrizes de navegador.

## Prioridades de integração e riscos encontrados

| Componente | Lacuna ou risco concreto | Verificação necessária |
| --- | --- | --- |
| `orion-suite.js`: `field`, `customerInputField`, `dialogForm`, `bodyHTML` | Formulários são recriados; vincular somente no carregamento inicial deixa modais sem máscara. Máscaras antigas de serviço/profissional podem disputar o mesmo evento. | Binding idempotente, remoção coordenada de listeners antigos, abertura/fechamento repetidos, input/caret e payload |
| Campos de comissão, cupom e meta | O mesmo `name` alterna entre reais, percentual, quantidade ou campo inativo. Um valor monetário não pode ser reinterpretado ao mudar a modalidade. | Rascunho separado por modo; nova modalidade vazia; limites por unidade; campos ocultos desabilitados |
| `orion-onboarding.js`: `catalogForm`, `capture`, `restore`, `conditionalFields` | Parser monetário próprio; renderização e restauração de rascunhos; campos de endereço e de produto condicionais. | Moeda exata, restauração sem perda, resposta CEP obsoleta ignorada, tipo e obrigatoriedade coerentes |
| `panel.html`: `renderForm`, `businessAddRepeater`, `commerceAddFields`, catálogo | Parte dos controles nasce em `createElement`, fora dos construtores da suíte. Há telefones individuais, lista de telefones, SKU, UUID e instantes com fuso distintos. | Integrar cada construtor/consumidor pelo contrato; não limpar globalmente strings |
| Público, landing e convite | Antes usavam validadores próprios e/ou máscara de telefone isolada. Consumidores agora dependem do core antes de executar. | Ordem dos scripts, allowlists, MIME/CSP/cache; UI formatada e payload canônico; seis dígitos preservando zeros |
| Datas nativas e área de ações do agendamento | Regressões anteriores de nascimento desproporcional, CTA esticado e espaço inferior. | Preservar `date`, `time`, `month`, `datetime-local`; executar os drivers de dimensionamento/rodapé sem relaxar asserts |
| Central e Marketing | Arquivos antigos permanecem no bundle, mas carregamento não prova montagem ativa. | Não reintroduzir Clientes, Respostas rápidas, Simulações ou blocos removidos do Marketing |

### Alcance da navegação atual

`orion-inbox.js` monta somente `OrionConversations`, com `toolsEnabled:false`. `#centralReplySelect` permanece no código da conversa, mas a carga de respostas rápidas depende da opção desabilitada. Os módulos `OrionCommunication` e `OrionAutomations` aparecem como assets/unmount no painel, sem montagem ativa no HEAD auditado. `OrionFlows` perdeu a entrada pela Central. O diálogo `showLoyalty` continua sem entrada demonstrada; o programa editável é `loyalty-inline`.

Os metadados legados `fields.professionals`, `fields.services`, `fields.professional_services`, `fields.working_hours`, `fields.working_breaks` e `fields.schedule_blocks` não viram novas telas: o seletor administrativo recebe apenas `authorized_staff` e `time_off`. Os campos dos demais recursos já são cobertos nas telas de gestão correspondentes. O cadeado de ocultar o rodapé é um botão indisponível, não um novo checkbox editável.

No perfil legado, “Processar pagamentos” e “Responder com voz” são placeholders desabilitados. Na landing, `#lead-website` é honeypot antispam; não recebe máscara nem deve se tornar visível. O segredo `#token` em `qr_connect.html` permanece `password` e não participa de normalizações de telefone/documento.

## Contratos semânticos

### Apresentação e valor enviado

| Família | Apresentação | Valor/contrato ao enviar | Particularidades a preservar |
| --- | --- | --- | --- |
| Nome e texto livre | Exemplo contextual, acentos e grafia informada | String conforme contrato original | Apóstrofos, hífens, quebras de linha, marca e capitalização não são dados inválidos |
| Busca | Texto livre | Query de busca | Nome, trecho de telefone, protocolo e ID; sem máscara rígida |
| Telefone único | `(00) 00000-0000` ou fixo `(00) 0000-0000` | E.164 nos contratos de telefone único, normalmente `+55…`; opcional vazio mantém `null` | DDD 55 não é DDI; excesso, letra, país estrangeiro ou ramal não podem sumir silenciosamente |
| Lista de telefones | Um número legível por item, separadores aceitos pelo formulário | Array no formato específico da API | `qc-senders` usa dígitos canônicos sem `+`; não aplicar a máscara de um único telefone à textarea inteira |
| Dinheiro | Unidade `R$` no rótulo **ou** prefixo visual, com valor `1.234,56` | Inteiro em centavos (`price_cents`, `amount_cents`, `monthly_goal_cents`, etc.) | `1234` e `1.234` significam 1234 reais; vazio difere de zero; sem `parseFloat` permissivo ou centavos implícitos |
| Percentual | Decimal brasileiro, unidade `%` | Decimal percentual dentro do limite específico | Comissão e cupom percentual não multiplicam por 100 nem recebem prefixo `R$` |
| Pontos, duração e contagem | Inteiro, unidade explícita quando útil | Inteiro dentro dos limites do schema | Pontos, estoque, minutos, dias e meta de atendimentos são unidades distintas |
| CPF | `000.000.000-00` | String de 11 dígitos com DV válido | Zero inicial preservado; documentos históricos só tolerados pelo contrato explícito de valor original inalterado |
| CNPJ | `00.000.000/0000-00` ou letras no formato correspondente | String canônica com DV válido; numérica ou alfanumérica | Não converter para número; o contrato alfanumérico é tratado pelo core e pelos testes backend próprios |
| CEP | `00000-000` | CEP canônico conforme API | Lookup não substitui número/complemento/sem número; resposta atrasada não sobrescreve outra edição |
| E-mail | `nome@email.com`, `type=email` quando o contrato permite | String normalizada conforme consumidor | Login `#username` continua usuário **ou** e-mail (`type=text`, `autocomplete=username`) |
| OTP | Texto de seis dígitos | String de seis dígitos | Zeros iniciais, colagem completa e `autocomplete=one-time-code`; não é número de cálculo |
| Data civil | Controle nativo de data, com locale do navegador | `AAAA-MM-DD` | Nascimento não vira instante UTC; valor bissexto e limites preservados |
| Hora, mês e instante | `time`, `month`, `datetime-local` nativos | `HH:mm`, `AAAA-MM` ou instante com fuso conforme API | Bloqueio/estoque/agendamento não compartilham uma conversão genérica de data |
| URL, slug, documento técnico | Exemplo específico do contrato | String específica | Slug transliterado/lowercase/hífens; SKU, UUID, senha, token e domínio não recebem “limpeza” de dados pessoais |
| Seleção, ativação, cor e arquivo | Controle proporcional ao contexto | Enum, booleano, cor ou upload existente | Não alterar permissões, limites de upload nem tratar booleanos como texto mascarado |

O perfil legado e a lista de chamadas autorizadas mantêm o suporte internacional que seus contratos já admitem. A máscara brasileira dos demais cadastros não deve converter nem rejeitar automaticamente esses números internacionais. O teste `fields-panel-legacy` cobre essa distinção, além da diferença entre estoque desconhecido e estoque zero.

As folgas legadas (`time_off`) interpretam o `datetime-local` no fuso IANA explícito da empresa, sem usar o fuso do dispositivo como fallback. Fuso ausente/inválido, horário inexistente e horário ambíguo durante mudança de offset impedem o envio. Os testes cobrem São Paulo, Manaus, Kathmandu e transições de Nova York. Os instantes do estoque comercial preservam o contrato anterior, que interpreta o controle no fuso do dispositivo; não houve conversão global desses consumidores.

### Campos cuja unidade ou presença muda

| Escopo / campo | Condição | Resultado esperado |
| --- | --- | --- |
| `professional` / `commission_percent` | Comissão padrão | Decimal percentual, 0–100 |
| `professional` / `commission_value_{service_id}` | `commission_kind_* = fixed` ou `percent` | Reais → `commission_fixed_cents`; percentual → `commission_percent`; rascunhos separados |
| `professional` / comissão por serviço | Modelo `per_service` e serviço selecionado | Campo editável somente quando aplicável; vínculo e configuração versionada preservados |
| `goal` / `target` | Métrica `revenue_cents` ou `appointments` | Dinheiro/centavos ou contagem inteira; não reinterpretar o valor de outra métrica |
| `goal` / `professional_id` | Escopo empresa | Inativo/oculto para meta da empresa; obrigatório para meta individual |
| `goal` / datas | Meta mensal ou meta histórica com período próprio | Recorrência mensal não ganha datas artificiais; metas legadas preservam seu período |
| `loyalty-inline` / `reward_value` | Cupom `fixed`, `percent` ou `free_service` | Moeda, percentual ou desabilitado; `reward_service_id` só ativo no serviço grátis |
| `loyalty-inline` / `spend_step` | Faixa personalizada já existente | Campo monetário visível; caso padrão mantém seu valor interno sem criar campo duplicado |
| Onboarding `location` | Atendimento presencial/híbrido ou remoto | Endereço exigido apenas quando aplicável; `number` desabilitado quando `no_number` |
| Onboarding `product` / `price` | `price_kind=fixed/from/on_request/custom` | Preço requerido somente nos modos que têm valor; vazio não vira zero |
| Onboarding `product` / `variant_id` | Mais de uma variante | Seletor só existe quando há escolha real; não multiplicar o inventário por variante |
| Onboarding `hours` / pausa | `has_break` | Início/fim da pausa habilitados e requeridos em conjunto |
| Onboarding `professional` / `self_provider` | Novo profissional | Opção de preenchimento do nome; não concede permissão administrativa |
| Agenda `block` | Horário, dia inteiro, repetição e frequência | Somente campos relevantes participam do envio; datas/horas/fuso continuam coerentes |
| Catálogo legado / `comPrice` | Preço fixo, a partir de ou sob consulta | Centavos apenas quando existe preço; SKU/atributos permanecem literais |
| Marketing / `rule_enabled_*`, `rule_template_*` | Grupo de regras selecionado | Família por evento, sem reinserir blocos removidos nem habilitar execução |

Na base auditada, `professionalForm` do onboarding **não possui telefone do profissional**. A máscara do telefone de profissionais se aplica ao campo existente em `professional` da suíte; não se deve declarar uma máscara em um campo inexistente da configuração inicial.

## Mapa de consumidores e endpoints

Nas abreviações abaixo, `T` é `/api/v1/tenants/{tenant_id}` e `M` é `T/management`. A API mantém validação de schema, permissões, isolamento da empresa e controle de versão; máscara no navegador não substitui essas verificações.

| Grupos | Consumidor / endpoint | Unidades e observações |
| --- | --- | --- |
| 1–3 | `panel.html`; `/auth/login`, `/auth/register`, `/auth/verify`, `/auth/password/forgot`, `/auth/password/reset` | Credenciais e OTP textuais, nenhum telefone inferido do usuário de login |
| 4 | `orion-invite.js`; `/api/v1/team-invitations/preview`, confirmação de e-mail em `/auth/verify` | Token só para o convite; código de seis dígitos separado; confirmar e-mail não aprova sozinho a permissão |
| 5 | `initializeForm`; `POST /api/public/sales-requests` | E-mail e telefone opcional canônicos; segmento/intenção enums; consentimento e honeypot preservados |
| 6 | `orion-public.js`; `/api/v1/public/sites/{slug}`, `/availability`, `/appointments` | Busca sem máscara; nascimento civil ISO; telefone E.164; quote e instante de agendamento preservados |
| 7 | `showCustomer`; `M/customers`, `M/customers/{id}`, `M/customers/{id}/services` | Telefone opcional, identidade do mesmo cliente, nascimento único, histórico somente leitura |
| 8 | `showProfessional`; `T/professionals`, `M/professionals/{id}/configure`, histórico profissional | Telefone privado E.164, percentuais/centavos, configuração versionada |
| 9 | `showInvitation`, `showTeamMember`; `M/team/invitations`, `M/team/members/{account_id}` | E-mail opcional do convite, papéis e ativações; link gerado readonly |
| 10 | `T/services`, `T/professional_services`, `M/services/{id}/addons`, `M/service-packages` | Preço em centavos; duração/preparação/limpeza em minutos; checkboxes são famílias por item |
| 11–13 | Lista da agenda e `T/appointments/hold`, `T/appointments/{id}/confirm` e ações de agendamento | Nome/telefone, data civil, slot com fuso, motivo; `final_total` em reais vira centavos |
| 14 | `M/block-series`, `T/time_off` e ações de série | Horários locais e intervalo de dias, frequência e seleção de dias; não converter UUID em número |
| 15–16 | `M/finance`, `/summary`, `/commissions`, `/advances`, `/settlements` | `amount_cents`, data/instante conforme contrato, enum de pagamento; totais da quitação são calculados |
| 17 | `M/performance`, `/performance/daily`, `M/goals` | Período do filtro respeitado; métrica determina unidade do objetivo e recorrência mensal |
| 18 | `M/site`, `/site/publication`, `/site/metrics`, `/site/slug-availability`; ViaCEP | Rascunho/publicação separados, CEP e endereço, telefone, links/slug e comodidades; número textual |
| 19 | `M/preferences` | Telefone, meta mensal em centavos, tipo/documento fiscal, nome fantasia/razão social e mensagens |
| 20 | `T/working_hours`, `T/working_breaks` | Hora local; quatro formulários distintos, somente edição da jornada expõe `weekday` |
| 21 | Consumidores `showGoogleCalendars`, `submitGoogleAction`, `submitGoogleEmail`; `T/google/*` e confirmação da conta | ID de calendário literal; OTP de e-mail não é código de calendário |
| 22 | Consulta administrativa de solicitações em `renderSiteRequests` | Filtros `q/status/intent`, acesso conforme perfil; não adiciona campo de contato novo |
| 23 | `M/marketing`, `/marketing/automations`, `/ai/generate-campaign` | Rascunhos, textos e data/hora planejada; revisão de campo não autoriza envio |
| 24 | `M/loyalty`, `/loyalty/rules`, `/loyalty/transactions`, `/loyalty/coupons`, `/ai/loyalty-ideas` | Pontos, reais/centavos, percentual e validade em dias; link de divulgação readonly |
| 25 | `M/ai/consultant`; busca local de ajuda | Texto livre e busca sem máscara |
| 26–28 | `/api/v1/onboarding/entry`, `T/onboarding` e operações por etapa | Snapshots, chaves idempotentes e rascunhos; preços em centavos, horas locais e CEP protegido contra corrida |
| 29 | `T/qr-channels` e ações específicas do canal | Nome do dispositivo, lista canônica de números, autorização explícita; criação não ativa assistente |
| 30 | `M/inbox/*` | Busca por texto/número, filtros de canal/estado, texto da conversa; somente Conversas disponível |
| 31 | `T/business/profile` | Perfil, telefone, fuso, capacidades e repetidores; regras e exemplos não alteram credenciais |
| 32 | `T/commerce/*` | Produtos, variantes, FAQ, atributos, preço em centavos, estoque e instantes no contrato existente do dispositivo, quantidade, contato e consulta por IDs |
| 33 | `/api/v1/qr/settings`, `/api/v1/qr/pair`, `/api/v1/qr/send` e consulta legada | Telefone único versus lista, texto de resposta, horários/enum de idioma; testes não enviam mensagem real |
| 34 | `T/authorized_staff`, `T/time_off`, simulador e `qr_connect.html` | Telefone autorizado; UUID e folgas no fuso IANA explícito da empresa; texto/áudio de simulação; token técnico preservado |

## Acessibilidade e apresentação a conferir

Os construtores comuns já oferecem rótulo associado ou envolvente, mas isso não garante cobertura dos inputs montados diretamente. Cada grupo precisa manter rótulo visível, `for`/`id` ou label envolvente; um `aria-label` específico é usado nos controles compactos de comissão e filtros. Placeholder é exemplo, não substituto do rótulo.

Erros precisam permanecer no contexto do formulário e associados ao campo por `aria-invalid` e `aria-describedby`, preservando ajuda já existente. A mensagem não deve ficar sobre o prefixo monetário ou dentro de um invólucro de altura fixa. Desabilitado e readonly não devem ganhar interação/limpeza; ícones e unidades não podem bloquear o input. Fotos, cores, uploads, toggles e seletores mantêm seus componentes específicos.

A matriz mínima é `360×800`, `390×844`, `430×932`, `768×1024`, `1024×768`, `1440×900`, em claro e escuro onde suportados. São **2 engines × 6 dimensões × 2 temas = 24 execuções** por cenário parametrizado. A execução WebKit é emulação de navegador, não teste em Safari de um iPhone físico. Altura reduzida de 420 px simula espaço de viewport; não demonstra sozinho teclado físico/mobile real, autofill de sistema ou zoom assistivo.

O novo driver da suíte percorre Cliente, Serviço, Financeiro, Profissionais/comissões, Preferências CPF/CNPJ, Site/CEP, Fidelização e Metas. Ele mede todos os `input/select/textarea` visíveis de cada formulário, incluindo estados inválidos e trocas de modalidade; não somente o exemplo de telefone. Os drivers existentes de confirmação/rodapé público mantêm as asserções originais e acrescentam as seis dimensões sem retirar os casos anteriores de 320/375 px.

## Evidências e gates

| Camada | Arquivos / comando | Resultado consolidado em 28/10/2026 |
| --- | --- | --- |
| Índice | `scripts/inventory_fields.py` em base Git e árvore atual | 420 indexadas, 378 ativas; 34 grupos; conjuntos de localizadores coincidentes na conferência inicial |
| Core DOM | `ui-tests/fields-core.test.mjs` | Incluído no agregado final de **722/722 aprovados**; 24 casos próprios do core |
| Consumidores suíte/acesso | `fields-integration`, `fields-auth`, `fields-conditional` | Incluídos no agregado final; 9 casos condicionais cobrem unidades, rascunhos e limites |
| Público/landing/convite | `fields-public-consumers`, `orion-landing`, `invite-verification` | Incluídos no agregado final; UI formatada, atributos nativos e payload canônico têm asserções próprias |
| Onboarding/canais | `fields-onboarding-channels`, harnesses existentes | Incluídos no agregado final; CEP sintético, corrida/timeout, rascunhos e lista de telefones cobertos |
| Legado | Gate `fields-panel-legacy` e testes existentes do painel | Incluído no agregado final; gate focal de 14/14 também passou com `TZ=UTC`, incluindo fuso da empresa e guarda readonly/disabled |
| Backend | `test_brazilian_form_contracts` e regressões de telefone/comissão/catálogo/onboarding/site/gestão | **285 aprovados**, 6 avisos de depreciação existentes do FastAPI, sem banco de produção |
| Assets | `tests/unit/test_fields_assets.py` e `test_live_suite_asset.py` | Incluídos nos 285 aprovados. Variante HTTP via ASGI preservada: allowlists, body/MIME/cache/nosniff, ordem dos scripts e CSP; o resultado final não depende da chamada direta de handlers |
| Suíte visual | `tests/e2e/browser_fields_standardization.py` + `fixtures/fields-panel.js` | Preparado e compilado; **não executado localmente**. Resultado depende da CI |
| Público visual | Nove `tests/e2e/browser_public_*.py` | Loaders atualizados para core; três payloads E.164; casos/asserts anteriores preservados. Resultado depende da CI |
| Acesso visual | `tests/e2e/browser_fields_access.py` | Preparado e compilado: 4 superfícies × 24 estados = 96 casos; fixtures específicas reaproveitam `fullState/entry` do harness; **não executado localmente** |
| Drivers anteriores da suíte | Financeiro, desempenho, combos, jornada, clientes, endereço/slug e preço do serviço | Sete loaders atualizados para core JS/CSS, sem alterar asserts; incluídos no gate de navegador |
| Regressões gerais | Todos os `ui-tests/*.test.mjs`; seleção Python do workflow | Local DOM: **722 aprovados, zero falhas, zero pulados**, em 66,8 s. Backend: **285 aprovados** em 5,85 s. As execuções de CI ainda estão pendentes |
| Revisão manual em prévia | Wrapper isolado com fontes reais e fetch sintético | Cliente/Serviço 390×844 claro, Preferências 390×844 escuro, Público 360×800 e onboarding 768×1024 claro; detalhes e limites abaixo |

### Execuções locais reproduzíveis

O comando DOM final foi `node --max-old-space-size=4096 --test --test-isolation=none ui-tests/*.test.mjs`. O log `/tmp/orion-fields-dom-verified.log` registra 722 testes, 722 aprovados, zero falhas, zero cancelados e zero pulados. Uma tentativa anterior com heap padrão de aproximadamente 2 GB terminou por falta de memória; ela não foi usada como resultado final. No workflow, a regressão geral continua em `npm test --prefix ui-tests`, com isolamento normal do runner Node e `NODE_OPTIONS=--max-old-space-size=4096`.

O log `/tmp/orion-fields-backend-full.log` registra 285 testes aprovados em 5,85 s. A seleção é a do job `contracts`: `test_brazilian_form_contracts`, `test_fields_assets`, `test_live_suite_asset`, `test_professional_phone`, `test_professional_commissions`, `test_commercial_catalog`, `test_site_address_slugs`, `test_onboarding` e `test_management_extras_unit`. As rotas de assets foram exercitadas por HTTP via ASGI; nenhuma dependência de produção foi acionada. Os seis avisos são de depreciação de `on_event` no FastAPI.

### Revisão manual CUA da prévia isolada

| Tela / viewport / tema | Evidência observada | Limite da evidência |
| --- | --- | --- |
| Cliente — 390×844, claro | Telefone formatado preservou DDD 55 e o envio em memória registrou `+5555991234567` | Dados sintéticos; não grava cadastro real |
| Serviço — 390×844, claro | Entrada `1.234` exibiu `1.234,00`; envio em memória registrou `123400` centavos e a lista mostrou `R$ 1.234,00` | Um caso manual de moeda; os demais modos têm testes DOM próprios |
| Preferências — 390×844, escuro | CNPJ alfanumérico conferido no formulário com a máscara correspondente | Não substitui execução de toda a matriz de temas/dimensões |
| Agendamento público — 360×800 | Campo nativo de nascimento manteve o valor civil ISO; a submissão foi verificada nos testes DOM | Chrome em inglês apresentou `MM/DD` no controle nativo; não foi demonstrada apresentação visual `DD/MM/AAAA` nessa sessão |
| Onboarding/endereço — 768×1024, claro | Após correção por container query, CEP/rua/número/complemento/bairro/cidade/UF mediram **387,5×44 px** cada. CEP sintético `01001000` preencheu Praça da Sé/Sé/São Paulo/SP e preservou número `123A` | ViaCEP respondido pela fixture; prova de layout e preenchimento, sem consulta externa nem persistência real |

### Correções encontradas na matriz de navegador

A rodada `5379b8d` confirmou 116/116 casos focais DOM, 722/722 na regressão DOM e 285/285 backend/assets. As 96 combinações de acesso (auth, onboarding, landing e convite) passaram em Chromium e WebKit na primeira rodada. O teste do preço do serviço passou nos seis casos de Chromium/WebKit em 320, 390 e 768 px após a fixture carregar o bootstrap real do core.

A matriz ampla identificou dois defeitos de apresentação, corrigidos na revisão seguinte:

- Os controles compactos de comissão tinham regras com ID que prevaleciam sobre o estilo móvel compartilhado. O ajuste específico mantém fonte de **16 px** e altura de **42 px**, confirmado em CUA em 390×844 com percentual e `R$ 1.234,00`.
- O rótulo acessível absoluto “Ações” da tabela escapava do seu contêiner de rolagem e aumentava a largura do documento no tablet. `.os-table-scroll` agora estabelece `position:relative`, mantendo sua rolagem própria. No painel completo de 768 px, a largura útil/rolagem do documento passou de **753/858 px para 753/753 px**; a tabela continua com 695 px dentro do contêiner de 415 px.

O teste de alvo do ícone de histórico usa as mesmas asserções, mas agora serve as fontes reais em uma origem HTTPS sintética, aguarda fontes e duas amostras estáveis de layout e lê geometria/hit test atomicamente. Os runners anteriores usavam Chrome 155, enquanto o Playwright fixado traz Chromium 141; os diagnósticos registram essa diferença, sem equiparar emulação a aparelho físico. A fixture DOM do profissional também aguarda a tarefa inicial de foco antes de simular interação, preservando a asserção de foco no telefone inválido.

Os harnesses legados QR passaram a carregar o core real antes do controlador do painel. Os 9 erros novos por dependência ausente foram eliminados. As **73 falhas restantes** coincidem literalmente com os nomes da base `ba563ef`, e as verificações antigas de migração/recorrência/cliente e a coleta sem Playwright têm seus resultados comparados no fechamento do PR. Esses pipelines gerais não devem ser descritos como todos verdes.

O workflow `.github/workflows/fields-standardization.yml` separa DOM, contratos/assets/inventário e navegador Chromium/WebKit. Fixtures não encaminham rede real, usam domínios `orion.test`/`example.invalid`, dados inventados e respostas em memória; nenhum teste exige conta real nem envia mensagem, lead ou agendamento de produção. Os artefatos de navegador registram geometria e capturas. A existência do workflow não significa que a execução passou: anexar o resultado e o SHA efetivo antes de usar esse resultado como gate de publicação.

### Fechamento da entrega

| Informação | Evidência / registro final |
| --- | --- |
| Revisões verificadas | Base `ba563ef`; CI DOM/backend em `5379b8d`; revisão final, árvore e merge registrados no PR #122 |
| DOM local | 722/722 aprovados; `/tmp/orion-fields-dom-verified.log` |
| Backend/assets local | 285 aprovados; `/tmp/orion-fields-backend-full.log` |
| CI DOM | 116 focais + 722 na regressão completa aprovados no run `40611671455`; resultado da revisão final nos checks do PR |
| CI backend/assets | 285 aprovados no run `40611671455`; resultado da revisão final nos checks do PR |
| CI Chromium | Matriz de seis dimensões, dois temas e regressões existentes; resultado final e artefatos vinculados no PR #122 |
| CI WebKit | Mesma matriz em emulação; resultado final e artefatos vinculados no PR #122 |
| Grupos revisados por CUA, dimensões e temas | Cliente/Serviço 390×844 claro; Preferências 390×844 escuro; Público 360×800; onboarding/endereço 768×1024 claro, conforme tabela acima |
| Browser real físico / autofill de sistema / zoom | Não demonstrado pelo ambiente sintético; não declarar aprovado sem prova adicional |
| Publicação / smoke no ambiente publicado | O fechamento do PR registra merge, deploy Railway e comparação de assets/HTML/CSP/cache nos três hosts configurados; prévia isolada não substitui essa verificação |

## Arquivos alterados

A comparação com `ba563ef` inclui os seguintes 75 arquivos de implementação, integração, testes e documentação:

- `.github/workflows/fields-standardization.yml`
- `app/brazilian_fields.py`
- `app/catalog_schemas.py`
- `app/commercial_schemas.py`
- `app/landing_api.py`
- `app/main.py`
- `app/management_preferences.py`
- `app/management_schemas.py`
- `app/onboarding_schemas.py`
- `app/professional_commissions.py`
- `app/public_site_api.py`
- `app/static/orion-channels.css`
- `app/static/orion-channels.js`
- `app/static/orion-fields.css`
- `app/static/orion-fields.js`
- `app/static/orion-invite.html`
- `app/static/orion-invite.js`
- `app/static/orion-onboarding.css`
- `app/static/orion-onboarding.js`
- `app/static/orion-public.html`
- `app/static/orion-public.js`
- `app/static/orion-suite.css`
- `app/static/orion-suite.js`
- `app/static/panel.html`
- `docs/fields-standardization-coverage.md`
- `landing/assets/orion-landing.js`
- `landing/index.html`
- `qr-bridge/test/panel-ui.test.mjs`
- `qr-bridge/test/wizard-journey.test.mjs`
- `scripts/inventory_fields.py`
- `tests/e2e/browser_customer_whatsapp_shortcut.py`
- `tests/e2e/browser_fields_access.py`
- `tests/e2e/browser_fields_standardization.py`
- `tests/e2e/browser_package_cards_compact.py`
- `tests/e2e/browser_public_amenities.py`
- `tests/e2e/browser_public_booking_footer.py`
- `tests/e2e/browser_public_confirmation_clean.py`
- `tests/e2e/browser_public_confirmation_sizing.py`
- `tests/e2e/browser_public_contact_theme.py`
- `tests/e2e/browser_public_hero_actions.py`
- `tests/e2e/browser_public_multiservice.py`
- `tests/e2e/browser_public_professional_smooth.py`
- `tests/e2e/browser_public_slot_periods.py`
- `tests/e2e/browser_service_editor_price.py`
- `tests/e2e/fixtures/fields-panel.js`
- `tests/e2e/test_finance_responsive.py`
- `tests/e2e/test_hours_responsive.py`
- `tests/e2e/test_performance_history_goals.py`
- `tests/e2e/test_site_address_slug.py`
- `tests/unit/test_brazilian_form_contracts.py`
- `tests/unit/test_fields_assets.py`
- `tests/unit/test_management_extras_unit.py`
- `ui-tests/booking-recovery.test.mjs`
- `ui-tests/channels-harness.mjs`
- `ui-tests/conversation-contact-names.test.mjs`
- `ui-tests/customer-whatsapp-shortcut.test.mjs`
- `ui-tests/fields-auth.test.mjs`
- `ui-tests/fields-conditional.test.mjs`
- `ui-tests/fields-core.test.mjs`
- `ui-tests/fields-integration.test.mjs`
- `ui-tests/fields-onboarding-channels.test.mjs`
- `ui-tests/fields-panel-legacy.test.mjs`
- `ui-tests/fields-public-consumers.test.mjs`
- `ui-tests/harness.mjs`
- `ui-tests/invite-verification.test.mjs`
- `ui-tests/mobile-atendimento.test.mjs`
- `ui-tests/onboarding-harness.mjs`
- `ui-tests/onboarding-save.test.mjs`
- `ui-tests/orion-landing.test.mjs`
- `ui-tests/orion-public.test.mjs`
- `ui-tests/panel-harness.mjs`
- `ui-tests/professional-phone.test.mjs`
- `ui-tests/public-confirmation-clean.test.mjs`
- `ui-tests/public-harness.mjs`
- `ui-tests/site-address-slug.test.mjs`

<!-- GENERATED_FIELD_INDEX_BEGIN -->

## Índice de campos por área

Gerado por `scripts/inventory_fields.py`, método `source-form-occurrences-v2`, revisão `working-tree`.

Uma família repetida por serviço, cliente, adicional ou dia aparece uma vez. Formulários distintos que compartilham um construtor aparecem separadamente.

### 1. Acesso — entrar — 3 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/panel.html` — `#loginForm` | `#username` (text); `#password` (password); `#rememberMe` (checkbox) |

### 2. Acesso — cadastro e confirmação — 5 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/panel.html` — `#registerForm` | `#registerName` (text); `#registerEmail` (email); `#registerPassword` (password); `#registerConfirm` (password) |
| `app/static/panel.html` — `#verifyForm` | `#verifyCode` (text) |

### 3. Acesso — recuperação e redefinição — 5 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/panel.html` — `#forgotForm` | `#forgotEmail` (email) |
| `app/static/panel.html` — `#resetForm` | `#resetEmail` (email); `#resetCode` (text); `#resetPassword` (password); `#resetConfirm` (password) |

### 4. Convite de equipe — 1 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-invite.html` — `#inviteVerifyForm` | `#inviteCode` (text) |

### 5. Site institucional — contato — 6 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `landing/index.html` — `#sales-request-form` | `#lead-name` (text); `#lead-email` (email); `#lead-phone` (tel); `#lead-segment` (select); `#lead-intent` (select); `[name=consent]` (checkbox) |

### 6. Catálogo e agendamento públicos — 6 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-public.html` — `servicos` | `#service-search` (search) |
| `app/static/orion-public.js` — `#booking-dialog / renderSelectedExtras()` | `.addon-option input` (checkbox) |
| `app/static/orion-public.js` — `#booking-dialog / renderTime()` | `#booking-day-input` (date) |
| `app/static/orion-public.js` — `#customer-form` | `[name=customer_name]` (text); `[name=customer_phone]` (tel); `[name=customer_birth_date]` (date) |

### 7. Clientes — 10 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-suite.js` — `[data-os-form=customer]` | `[name=email]` (email); `[name=birth_date]` (date); `[name=neighborhood]` (text); `[name=city]` (text); `[name=name]` (text); `[name=phone]` (tel); `[name=instagram]` (text); `[name=active]` (checkbox); `#osField-notes` (textarea) |
| `app/static/orion-suite.js` — `renderRoute()` | `#osCustomerSearch` (search) |

### 8. Profissionais e histórico — 13 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-suite.js` — `[data-os-form=professional]` | `[name=name]` (text); `[name=specialty]` (text); `[name=color]` (color); `[name=commission_mode]` (select); `[name=image_url]` (file); `[name=accepts_appointments]` (checkbox); `[name=active]` (checkbox); `[name=service_ids]` (checkbox); `[name=commission_kind_'+esc(service.id)+']` (select); `[name=commission_value_'+esc(service.id)+']` (text); `#osField-phone_e164` (tel); `#osField-commission_percent` (text) |
| `app/static/orion-suite.js` — `showProfessionalHistory()` | `#osProfessionalHistoryStatus` (select) |

### 9. Equipe e permissões — 4 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-suite.js` — `[data-os-form=invitation]` | `[name=email]` (email); `[name=role]` (select) |
| `app/static/orion-suite.js` — `[data-os-form=team-member]` | `[name=role]` (select); `[name=active]` (checkbox) |

### 10. Serviços e combos — 22 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-suite.js` — `[data-os-form=service]` | `[name=name]` (text); `[name=category]` (text); `[name=duration_min]` (number); `[name=price]` (text); `[name=color]` (color); `[name=image_url]` (file); `[name=description]` (textarea); `[name=prep_min]` (number); `[name=cleanup_min]` (number); `[name=active]` (checkbox); `[name=price_mode]` (radio); `[name=professional_ids]` (checkbox); `[name=addon_ids]` (checkbox); `[name=addon_duration_'+esc(service.id)+']` (checkbox) |
| `app/static/orion-suite.js` — `[data-os-form=package]` | `[name=name]` (text); `[name=price]` (text); `[name=active]` (checkbox); `[name=featured]` (checkbox); `[name=description]` (textarea); `[name=image_url]` (file); `[name=service_ids]` (checkbox) |
| `app/static/orion-suite.js` — `renderServices()` | `[data-os-service-toggle]` (checkbox) |

### 11. Agenda — filtros — 4 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-suite.js` — `agendaFrame()` | `#osAgendaDatePicker` (date); `#osAgendaSearch` (search); `#osAgendaProfessional` (select); `#osAgendaStatus` (select) |

### 12. Agenda — novo agendamento — 6 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-suite.js` — `[data-os-form=appointment]` | `[name=customer_phone]` (tel); `[name=day]` (date); `[name=professional_id]` (select); `#osBookingCustomer` (search); `#osBookingSlot` (select); `[data-os-book-addon]` (checkbox) |

### 13. Agenda — ações e conclusão — 6 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-suite.js` — `[data-os-form=complete-appointment]` | `[name=final_total]` (text); `[name=coupon_id]` (select) |
| `app/static/orion-suite.js` — `[data-os-form=appointment-action]` | `[name=reason]` (textarea); `[name=day]` (date); `[name=requested_by]` (radio); `#osRescheduleSlot` (select) |

### 14. Agenda — bloqueios e séries — 13 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-suite.js` — `[data-os-form=block]` | `[name=professional_id]` (select); `[name=block_type]` (select); `[name=day]` (date); `[name=start_time]` (time); `[name=end_time]` (time); `[name=repeat]` (checkbox); `[name=frequency]` (select); `[name=until_date]` (date); `[name=from_day]` (date); `[name=to_day]` (date); `[name=reason]` (text); `[name=weekdays]` (checkbox) |
| `app/static/orion-suite.js` — `[data-os-form=delete-series]` | `[name=scope]` (select) |

### 15. Financeiro — lançamentos e filtros — 11 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-suite.js` — `[data-os-form=finance]` | `[name=kind]` (select); `[name=category]` (text); `[name=amount]` (text); `[name=day]` (date); `[name=description]` (text); `[name=payment_method]` (select); `[name=status]` (select); `[name=notes]` (textarea) |
| `app/static/orion-suite.js` — `financeMonthPicker()` | `#osFinanceMonth` (month) |
| `app/static/orion-suite.js` — `renderFinance()` | `#osFinanceKind` (select); `#osFinanceStatus` (select) |

### 16. Financeiro — vales e quitação — 10 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-suite.js` — `[data-os-form=settlement]` | `[name=payment_method]` (select); `[name=day]` (date); `[name=notes]` (textarea) |
| `app/static/orion-suite.js` — `[data-os-form=staff-finance]` | `[name=professional_id]` (select); `[name=amount]` (text); `[name=day]` (date); `[name=description]` (text); `[name=payment_method]` (select); `[name=notes]` (textarea) |
| `app/static/orion-suite.js` — `renderAdvances()` | `#osAdvanceStatus` (select) |

### 17. Desempenho e metas — 10 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-suite.js` — `[data-os-form=goal]` | `[name=scope]` (select); `[name=professional_id]` (select); `[name=metric]` (select); `[name=target]` (text); `[name=start_date]` (date); `[name=end_date]` (date); `[name=active]` (checkbox) |
| `app/static/orion-suite.js` — `periodFilters()` | `#osPeriodStart` (date); `#osPeriodEnd` (date) |
| `app/static/orion-suite.js` — `performancePeriodFilters()` | `#osPerformancePeriod` (select) |

### 18. Meu site — conteúdo, link e endereço — 26 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-suite.js` — `[data-os-form=site]` | `[name=slug]` (text); `[name=postal_code]` (text); `[name=address]` (text); `[name=address_number]` (text); `[name=address_no_number]` (checkbox); `[name=address_complement]` (text); `[name=neighborhood]` (text); `[name=city]` (text); `[name=state]` (select); `[name=maps_url]` (url); `[name=title]` (text); `[name=contact_phone]` (tel); `[name=logo_url]` (file); `[name=cover_url]` (file); `[name=theme_color]` (color); `[name=description]` (textarea); `[name=show_prices]` (checkbox); `[name=whatsapp_phone]` (tel); `[name=instagram_url]` (text); `[name=facebook_url]` (text); `[name=tiktok_url]` (text); `[name=external_url]` (url); `[name=amenity_wifi]` (checkbox); `[name=amenity_parking]` (checkbox); `[name=amenity_accessibility]` (checkbox); `[name=amenity_children]` (checkbox) |

### 19. Configurações — preferências — 11 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-suite.js` — `[data-os-form=business-preferences]` | `[name=tax_document]` (text); `[name=contact_phone]` (tel); `[name=monthly_goal]` (text); `[name=tax_document_kind]` (select); `[name=trade_name]` (text); `[name=legal_name]` (text); `[name=google_review_url]` (url); `[name=google_review_message]` (textarea); `[name=auto_review_requested]` (checkbox); `[name=weekly_summary_requested]` (checkbox) |
| `app/static/orion-suite.js` — `renderSettings()` | `#osSettingsSection` (select) |

### 20. Configurações — horários e pausas — 10 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-suite.js` — `[data-os-form=hours-edit]` | `[name=weekday]` (select); `[name=start_time]` (time); `[name=end_time]` (time) |
| `app/static/orion-suite.js` — `[data-os-form=hours]` | `[name=start_time]` (time); `[name=end_time]` (time) |
| `app/static/orion-suite.js` — `[data-os-form=pause]` | `[name=start_time]` (time); `[name=end_time]` (time) |
| `app/static/orion-suite.js` — `[data-os-form=pause-edit]` | `[name=start_time]` (time); `[name=end_time]` (time) |
| `app/static/orion-suite.js` — `settingsHours()` | `#osHoursProfessional` (select) |

### 21. Configurações — Google Agenda — 2 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-suite.js` — `[data-os-form=google-calendar]` | `[name=calendar_id]` (select) |
| `app/static/orion-suite.js` — `[data-os-form=google-email]` | `[name=code]` (text) |

### 22. Solicitações do site — 3 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-suite.js` — `renderSiteRequests()` | `#osSiteRequestQuery` (search); `#osSiteRequestStatus` (select); `#osSiteRequestIntent` (select) |

### 23. Marketing IA — 11 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-suite.js` — `[data-os-form=automations]` | `[name=vacancy_requested]` (checkbox); `[name=recovery_days]` (select); `[name=rule_enabled_{event}]` (checkbox); `[name=rule_template_{event}]` (textarea) |
| `app/static/orion-suite.js` — `[data-os-form=ai-campaign]` | `[name=brief]` (textarea); `[name=tone]` (radio) |
| `app/static/orion-suite.js` — `[data-os-form=campaign]` | `[name=name]` (text); `[name=audience]` (select); `[name=message]` (textarea); `[name=scheduled_day]` (date); `[name=scheduled_time]` (time) |

### 24. Fidelização — 16 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-suite.js` — `[data-os-form=loyalty-inline]` | `[name=points_per_step]` (number); `[name=points_per_visit]` (number); `[name=spend_step]` (text); `[name=reward_points]` (number); `[name=reward_type]` (select); `[name=reward_value]` (text); `[name=reward_service_id]` (select); `[name=reward_valid_days]` (number); `[name=active]` (checkbox) |
| `app/static/orion-suite.js` — `[data-os-form=loyalty-ideas]` | `[name=goal]` (textarea) |
| `app/static/orion-suite.js` — `[data-os-form=loyalty-points]` | `[name=customer_id]` (select); `[name=operation]` (select); `[name=points]` (number); `[name=rule_id]` (select); `[name=reason]` (text) |
| `app/static/orion-suite.js` — `renderLoyalty()` | `#osCouponStatus` (select) |

### 25. Consultor IA e ajuda — 2 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-suite.js` — `[data-os-form=ai-consultant]` | `#osConsultorQuestion` (textarea) |
| `app/static/orion-suite.js` — `renderHelp()` | `#osHelpSearch` (search) |

### 26. Onboarding — negócio e endereço — 21 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-onboarding.js` — `[data-ob-form=create]` | `[name=name]` (text); `[name=niche]` (select); `[name=activity]` (select); `[name=contact_phone]` (tel); `[name=timezone]` (select); `#ob-other_niche` (text) |
| `app/static/orion-onboarding.js` — `[data-ob-form=business]` | `[name=name]` (text); `[name=niche]` (select); `[name=activity]` (select); `[name=contact_phone]` (tel); `[name=timezone]` (select); `#ob-other_niche` (text) |
| `app/static/orion-onboarding.js` — `[data-ob-form=location]` | `[name=modality]` (select); `[name=postal_code]` (text); `[name=street]` (text); `[name=number]` (text); `[name=no_number]` (checkbox); `[name=complement]` (text); `[name=neighborhood]` (text); `[name=city]` (text); `[name=state]` (select) |

### 27. Onboarding — profissionais e catálogo — 21 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-onboarding.js` — `[data-ob-form=professional]` | `[name=name]` (text); `[name=specialty]` (text); `[name=active]` (checkbox); `[name=accepts_appointments]` (checkbox); `[name=self_provider]` (checkbox) |
| `app/static/orion-onboarding.js` — `[data-ob-form=service]` | `[name=name]` (text); `[name=description]` (text); `[name=price]` (text); `[name=duration_min]` (text); `[name=prep_min]` (text); `[name=cleanup_min]` (text); `[name=active]` (checkbox); `[name=professional_ids]` (checkbox) |
| `app/static/orion-onboarding.js` — `[data-ob-form=product]` | `[name=name]` (text); `[name=description]` (text); `[name=variant_id]` (select); `[name=variant_name]` (text); `[name=price_kind]` (select); `[name=price]` (text); `[name=active]` (checkbox); `[name=variant_active]` (checkbox) |

### 28. Onboarding — jornada, canais e revisão — 14 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-onboarding.js` — `[data-ob-form=hours]` | `[name=professional_id]` (select); `[name=start_time]` (time); `[name=end_time]` (time); `[name=has_break]` (checkbox); `[name=break_start]` (time); `[name=break_end]` (time); `[name=replace_existing]` (checkbox); `[name=weekdays]` (checkbox) |
| `app/static/orion-onboarding.js` — `[data-ob-form=channels]` | `[name=assistant_name]` (text); `[name=style]` (select); `[name=length]` (select); `[name=emoji]` (select); `[name=capabilities]` (checkbox) |
| `app/static/orion-onboarding.js` — `[data-ob-form=review]` | `[name=acknowledged]` (checkbox) |

### 29. Canais WhatsApp — 3 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-channels.js` — `[data-qc-form=create]` | `#qc-label` (text); `#qc-senders` (textarea) |
| `app/static/orion-channels.js` — `[data-qc-form=confirm]` | `[name=authorize_assistant]` (checkbox) |

### 30. Central — Conversas — 6 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/orion-conversations.js` — `render` | `#centralSearch` (search); `[name=channel]` (select); `[name=mode]` (select); `[name=unread]` (checkbox); `#centralFind` (search); `#centralText` (textarea) |

### 31. Legado acessível — perfil da empresa — 35 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/panel.html` — `#businessForm` | `#bizName` (text); `#bizUnit` (text); `#bizCategories` (text); `#bizAssistant` (text); `#bizDescription` (textarea); `#bizAddress` (text); `#bizTimezone` (text); `#bizContact` (tel); `#bizStyle` (select); `#bizEmoji` (select); `#bizLength` (select); `#bizFormality` (select); `#bizCommercial` (select); `#bizAvoid` (textarea); `#bizCapCatalog` (checkbox); `#bizCapAppointments` (checkbox); `#bizCapReschedule` (checkbox); `#bizCapHuman` (checkbox); `#bizCapProductOrders` (checkbox); `#bizFulfillmentPickup` (checkbox); `#bizFulfillmentDelivery` (checkbox); `#bizObjQuestions` (checkbox); `#bizObjChoose` (checkbox); `#bizObjComplements` (checkbox); `#bizObjBooking` (checkbox); `#bizObjHuman` (checkbox) |
| `app/static/panel.html` — `page-empresa` | `#businessVersion` (select) |
| `app/static/panel.html` — `#businessForm / businessRepeatConfig.hours` | `{weekday}` (select); `{opens}` (time); `{closes}` (time) |
| `app/static/panel.html` — `#businessForm / businessRepeatConfig.faqs` | `{question}` (text); `{answer}` (textarea) |
| `app/static/panel.html` — `#businessForm / businessRepeatConfig.policies` | `{topic}` (text); `{description}` (textarea) |
| `app/static/panel.html` — `#businessForm / businessRepeatConfig.differentiators` | `{text}` (text) |

### 32. Legado acessível — catálogo e pedidos — 35 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/panel.html` — `page-catalogo` | `#comProduct` (select); `#comVariant` (select); `#comAccept` (checkbox); `#comLookupQuote` (text); `#comLookupOrder` (text) |
| `app/static/panel.html` — `#comProductForm` | `#comProductName` (text); `#comDescription` (textarea); `#comSynonyms` (textarea); `#comBenefits` (textarea); `#comProductActive` (checkbox) |
| `app/static/panel.html` — `#comVariantForm` | `#comVariantName` (text); `#comSku` (text); `#comPriceKind` (select); `#comPrice` (text); `#comVariantActive` (checkbox) |
| `app/static/panel.html` — `#comStockForm` | `#comStockMode` (select); `#comStockQuantity` (number); `#comStockSource` (text); `#comStockObserved` (datetime-local); `#comStockUntil` (datetime-local) |
| `app/static/panel.html` — `#comComplementForm` | `#comComplementTarget` (select); `#comComplementReason` (textarea); `#comComplementApproved` (checkbox) |
| `app/static/panel.html` — `#comPlanForm` | `#comBasketVariant` (select); `#comBasketQuantity` (number); `#comCustomer` (text); `#comContact` (tel); `#comValidity` (number); `#comFulfillmentMode` (select); `#comPickupAppointment` (select) |
| `app/static/panel.html` — `#comProductForm / commerceAddFields(faq)` | `{question}` (text); `{answer}` (textarea) |
| `app/static/panel.html` — `#comVariantForm / commerceAddFields(attribute)` | `{key}` (text); `{value}` (text) |
| `app/static/panel.html` — `#comPlanForm / commerceRenderBasket()` | `{quantity}` (number) |

### 33. Legado acessível — WhatsApp e conversa — 15 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/panel.html` — `#deviceSettingsForm` | `#settingsChatbot` (checkbox); `#settingsTranscription` (checkbox); `#settingsLanguage` (select); `#settingsBlockCalls` (checkbox); `#settingsCallReply` (checkbox); `#settingsReplyText` (textarea); `#settingsAllowedCallers` (textarea); `#settingsCallSchedule` (checkbox); `#settingsStart` (time); `#settingsEnd` (time); `#settingsWeekdays` (select) |
| `app/static/panel.html` — `#pairForm` | `#pairPhone` (tel) |
| `app/static/panel.html` — `page-conversas` | `#chatSearch` (search) |
| `app/static/panel.html` — `#sendForm` | `#chatPhone` (tel); `#chatText` (textarea) |

### 34. Administração, simulação e segredo técnico — 12 ocorrências

| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |
| --- | --- |
| `app/static/panel.html` — `page-ai` | `#aiProfile` (select); `#aiStyle` (select) |
| `app/static/panel.html` — `#aiForm` | `#aiInput` (textarea) |
| `app/static/panel.html` — `#aiAudioForm` | `#aiAudioFile` (file) |
| `app/static/panel.html` — `page-cadastros` | `#resource` (select) |
| `app/static/panel.html` — `#recordForm / resource=authorized_staff` | `[name=phone_e164]` (tel); `[name=role]` (text); `[name=active]` (checkbox) |
| `app/static/panel.html` — `#recordForm / resource=time_off` | `[name=professional_id]` (text); `[name=starts_at]` (datetime-local); `[name=ends_at]` (datetime-local); `[name=reason]` (text) |

### Controles fora do total preenchível ativo

| Estado | Arquivo / escopo | Campos |
| --- | --- | --- |
| disabled-placeholder | `app/static/panel.html` — `#businessForm` | `<unidentified>` (checkbox); `<unidentified>` (checkbox) |
| dormant | `app/static/orion-suite.js` — `[data-os-form=loyalty]` | `[name=name]` (text); `[name=earning_mode]` (select); `[name=points_per_visit]` (number); `[name=spend_step]` (text); `[name=points_per_step]` (number); `[name=reward_points]` (number); `[name=reward_description]` (text); `[name=active]` (checkbox) |
| readonly | `app/static/orion-suite.js` — `showInvitationResult()` | `<unidentified>` (text) |
| readonly | `app/static/orion-suite.js` — `loyaltyShare()` | `<unidentified>` (text) |
| dormant | `app/static/orion-conversations.js` — `render` | `#centralReplySelect` (select) |
| dormant | `app/static/orion-automations.js` — `render` | `[name=channel_id]` (select); `[data-oa-campaign]` (select) |
| dormant | `app/static/orion-automations.js` — `execution` | `[name=daily_limit]` (number); `[name=enabled]` (checkbox); `[name=acknowledged]` (checkbox) |
| dormant | `app/static/orion-automations.js` — `load-consent` | `[name=phone]` (tel) |
| dormant | `app/static/orion-automations.js` — `consent` | `[name=operational_opt_in]` (checkbox); `[name=marketing_opt_in]` (checkbox); `[name=birthday_opt_in]` (checkbox); `[name=evidence]` (text) |
| dormant | `app/static/orion-automations.js` — `contact-pause` | `[name=pause_acknowledged]` (checkbox) |
| dormant | `app/static/orion-automations.js` — `approve` | `[name=acknowledged]` (checkbox) |
| dormant | `app/static/orion-communication.js` — `render` | `[name=q]` (text); `[name=event]` (select); `[name=customer]` (select); `[name=appointment]` (select); `[name=weekdays]` (checkbox); `[name=holidays]` (textarea); `[name=birthday_leap_day]` (select); `[name=hosts]` (textarea) |
| dormant | `app/static/orion-communication.js` — `communication-policy` | `[name=operational_start_hour]` (number); `[name=operational_end_hour]` (number); `[name=marketing_start_hour]` (number); `[name=marketing_end_hour]` (number) |
| dormant | `app/static/orion-flows.js` — `render` | `[name=name]` (text); `[data-flow-kind]` (select); `[data-flow-label]` (text) |
| technical | `app/static/qr_connect.html` — `page` | `#token` (password) |
| technical-honeypot | `landing/index.html` — `#sales-request-form` | `#lead-website` (text) |

<!-- GENERATED_FIELD_INDEX_END -->
