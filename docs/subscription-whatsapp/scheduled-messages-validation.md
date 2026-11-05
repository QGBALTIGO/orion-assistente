# Etapa 08 — validação de Mensagens Programadas

Status em 2026-11-05: **módulo principal publicado, qualificado e conferido ao vivo**.
A [PR 260](https://github.com/QGBALTIGO/orion-assistente/pull/260) foi integrada;
produção `da3a0086a038f0f84024cc38c5960d3a2935ffbe`, árvore qualificada
`830a9a262bb4b69a826739cc7457185fc2f7af88`. O ajuste isolado do texto de ajuda
do card Fila foi publicado na PR 261 e também conferido, conforme a última seção.
A etapa 07 foi encerrada antes desta observação; a etapa 09 somente foi
iniciada após a conclusão da conferência descrita abaixo.

## Referência e limite da observação

Referência aberta na sessão conectada: `https://botbot.chat/user/schedule-message?channel=whatsapp`.
O [registro de observação](scheduled-messages-observation.md) descreve a lista,
abas, filtros e formulário exercitados sem salvar um agendamento nem enviar
mensagens. O [contrato](scheduled-messages-contract.md) distingue o que foi
observado das decisões explícitas do Orion.

Não havia ocorrências na referência para exercitar criação efetiva, histórico,
edição, cancelamento, envio, recorrência ou o alcance do botão global.
Não se afirma equivalência comprovada de comportamentos privados não observados.
Nenhum complemento foi comprado e nenhum acesso pago foi contornado.

## Adaptação implementada

A rota `programadas` usa a navegação, os campos, tabelas, diálogos, tipografia,
temas e componentes existentes do Orion. A permissão `scheduled_messages.manage`
se aplica somente ao workspace `subscription_services` da empresa ativa de
`servico_assinaturas`, com o proprietário autenticado.

O formulário conserva título, um dispositivo, grupos e contatos, seleção total,
data/horário, fuso da empresa, intervalos de 10 a 60 segundos, estimativa,
repetição a cada 30 dias e texto próprio ou um dos oito tipos de template
existentes. A seleção resolve contatos existentes no servidor, elimina
telefones repetidos e preserva o nome e as fontes da audiência revisada.

A revisão não cria uma série nem um efeito de envio. Confirmar usa a mesma
chave de idempotência, a revisão vigente e autorização explícita. Uma resposta
perdida é recuperada por consulta, inclusive após recarregar o painel. Uma
consulta negativa não autoriza inventar uma nova chave para o mesmo pedido.
As requisições de leitura podem ser abortadas ao trocar de contexto; uma
mutação já enviada conserva sua identidade de recuperação.

A execução reutiliza o poll privado do dispositivo. Agendamentos futuros
ficam no banco, sem efeitos de transporte prematuros. Campanhas e Programadas
compartilham a serialização, o espaçamento e o bloqueio por contato. Cada parte
da mensagem exige contexto, fonte e permissão atuais; cancelamento e opt-out
interrompem o trabalho pendente. Um recibo aceito é preservado; resultado
incerto não gera repetição automática nem reenvio pelo histórico genérico.

A recorrência usa a data de origem mais 30 dias por ciclo, no fuso confirmado,
e cria somente uma próxima ocorrência depois que a anterior termina. Não
inclui contatos novos nem reproduz ciclos perdidos. Horários locais ambíguos
ou inexistentes exigem atenção. Pendências de dias anteriores precisam de
liberação explícita; não bloqueiam a seleção de ocorrências devidas de hoje.

## Banco e publicação

Migração: arquivo `0069_subscription_scheduled_messages.py`, revisão curta
`0069_subscription_schedules`, após `0068_subscription_campaigns`.

As quatro tabelas privadas conservam série, ocorrência, destinatários e pedidos.
Chaves compostas vinculam empresa, série, ocorrência, destinatário e efeito.
Edições anteriores à admissão mantêm a revisão anterior dos destinatários
cancelada e inserem a nova, sem apagar o histórico. O runtime e as projeções
selecionam o hash da revisão atual.

O predeploy exige o catálogo anterior íntegro e backup privado verificado com
leitura integral do checksum remoto antes da migração. A API e o worker usam
somente verificações de catálogo em modo de leitura, depois da remoção das
credenciais temporárias. Os contratos de todas as etapas anteriores permanecem
obrigatórios. A migração foi aplicada pelo predeploy da publicação qualificada,
com a sequência observada registrada abaixo. Os testes de integração usaram
banco descartável; a conferência ao vivo não criou agendamentos nem enviou
mensagens reais.

## Histórico da validação local

- Permissões, histórico e inicialização: 116 testes passaram na primeira rodada.
- Publicação de Campanhas e Programadas: 64 testes passaram.
- Ampliação da cadeia de inicialização: 142 testes passaram após incluir a nova
  barreira nos mocks, mantendo os bloqueios por falha de cada contrato.
- DOM existente: 57 testes passaram; o extrator do adaptador de API foi
  atualizado para seu quarto argumento opcional. Os dois testes do adaptador
  real passaram, incluindo AbortSignal e prova restrita de versão desatualizada.
- Rodada Python integrada inicial: 2.032 passaram e dois testes antigos de
  opt-out precisaram reconhecer a invalidação adicional de Programadas.
  Após a correção, os 81 testes de runtime de Campanhas e wiring passaram.
- Interface nova: 45 testes DOM passaram na rodada final. Foram preparados 34 casos de Chrome
  e WebKit para telas, temas, recuperação e mudança de contexto.
- Política e API novas: 120 testes passaram na rodada final do responsável.
- Conjunto local do módulo: 285 testes passaram antes da última regressão de
  encerramento externo; runtime de Programadas passou em 98 casos após a correção.
- Ponte focada: 72 testes passaram, incluindo 39 novos de Programadas.
- A revisão final encontrou destinatários que continuavam em execução depois
  de seus efeitos serem encerrados pela alteração/remoção do dispositivo.
  Programadas e Campanhas agora projetam esses efeitos terminais antes do
  bloqueio compartilhado da fila. O cancelamento também reconcilia a ocorrência,
  preservando partes realmente em andamento e recibos já existentes.
  As regressões reais desse caminho estão no gate PostgreSQL descartável.

Estas contagens são rodadas, com sobreposição; não representam uma soma de
casos únicos nem substituem o resultado final da integração contínua.

## Primeira candidata no GitHub — histórico encerrado

PR 260, commit `2c6cd476de074099699e0e100637f114849f95e8`, árvore
`6eace88d5aeb0616bea7895a01b4c6b64f7ceb8a`. O workflow dedicado
`42024268629` usou o merge ref `bd3ed34d9f7a344715b3460f9c7acfc2716c9e09`,
com a mesma árvore e a base publicada `19fbe9a98cc59dbc927f69517666f77ec2eb8d6b`.

- API/contratos: 2.257 passaram; ponte: 451 passaram; DOM: 423 passaram.
- Chrome e WebKit: 68 passaram e um caso falhou em cada navegador na
  recuperação após recarregar. O bootstrap do painel não preservava o estado
  de recuperação de Programadas. A correção inclui esse estado na normalização
  inicial e um teste que executa o trecho real do bootstrap com o módulo real.
- Os seis casos de layout por navegador passaram. As 48 capturas de editor,
  revisão e detalhe foram inspecionadas nos dois temas e em 390, 768 e 1440 px.
  A falha de recuperação interrompeu os casos posteriores nessa rodada. Essa
  pendência foi encerrada pela segunda candidata, com 91 casos aprovados em
  cada navegador.
- O PostgreSQL real de Dispositivos revelou ausência de acesso ao catálogo de
  clientes pelo papel de suporte. A migração foi corrigida para conceder somente
  SELECT nas sete colunas usadas, com política de leitura limitada a empresas
  de assinaturas. A barreira exige esse contrato; os testes exercitam revogação,
  ampliação indevida, proteção dos demais campos e isolamento por nicho.

O PostgreSQL dedicado terminou com 309 testes aprovados e 121 falhas: 75
causadas pelo grant ausente de clientes e 46 na verificação inicial do novo
catálogo, antes dos testes de deriva. O catálogo predecessor passou. A segunda
candidata ajustou também o reconhecimento de `IS DISTINCT FROM` quando o
PostgreSQL normaliza a coluna varchar com cast para text, e o teste inicial
reporta apenas metadados das seções/fragmentos ausentes se o catálogo falhar.
A segunda rodada começou pelo novo contrato com interrupção no primeiro
erro; como os casos passaram, executou também todas as suítes anteriores.

Esta primeira candidata não foi publicada em produção. As falhas descritas
acima foram corrigidas e deixaram de ocorrer na qualificação seguinte.

## Qualificação final da PR 260

O [workflow dedicado 42027092137](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42027092137)
passou nos seis gates. A candidata foi o head
`3110978cf8bd0d7f84e9872b9e436f2f28aa3a08`; o checkout de integração foi
`7333bf5847c1ea847f7b9e323d32b761898d4dc1`, sobre a base publicada
`19fbe9a98cc59dbc927f69517666f77ec2eb8d6b`. A árvore testada
`830a9a262bb4b69a826739cc7457185fc2f7af88` foi preservada pelo merge
`da3a0086a038f0f84024cc38c5960d3a2935ffbe`.

| Gate | Job | Resultado |
|---|---|---|
| API, políticas, motor e contratos Python | `127243903450` | 2.257 aprovados |
| PostgreSQL real, migração, RLS e concorrência | `127243902966` | 439 aprovados |
| Ponte, efeitos e isolamento do legado | `127243903693` | 451 aprovados |
| DOM, formulários, recuperação e navegação | `127243903567` | 424 aprovados |
| Chrome | `127243903379` | 91 aprovados |
| WebKit | `127243903420` | 91 aprovados |
| Total do workflow dedicado | | **3.753 execuções aprovadas** |

As contagens representam execuções dos conjuntos, com cobertura sobreposta,
e não uma quantidade de casos funcionais únicos. Não houve falha no workflow
dedicado final. Os testes de banco exercitaram cookies/CSRF, proprietários,
isolamento, concorrência de confirmação, recuperação, edições e revisões,
mídia privada, opt-out, horários, recorrência, recibos, alteração/remoção de
dispositivo e mudanças reais dos catálogos e grants no banco descartável.

## Comparação do CI geral e passivo histórico

O acompanhamento geral, encerrado em 2026-11-05 às 12:44:33 UTC, exclui o
workflow dedicado acima. Foram 26 workflows: 18 aprovados e oito com falhas;
nos 64 jobs, **50 foram aprovados e 14 conservaram falhas históricas**. Nenhum
job ficou pendente. O CI geral permanece com esse passivo e não é descrito
como integralmente aprovado.

A [suíte FATIA completa da candidata](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42027091686/job/127243900902)
terminou com **9.230 aprovados, 169 falhas, 43 erros e 10 ignorados**. As 121
falhas introduzidas na primeira candidata desapareceram: 75 vinham do acesso
a `customers` e 46 da verificação inicial do catálogo de Programadas. Não
foram encontradas novas identidades de falha ou erro no comparativo final.

Os 212 registros restantes da FATIA, correspondentes às 169 falhas e aos
43 erros, coincidem em identidade, fase e multiplicidade com o histórico da
[PR 258, run 41934922417](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41934922417/job/126940847884).
**Limite da comparação:** a FATIA completa da base PR 259, job
`126999382260`, atingiu o timeout de 20 minutos sem produzir resumo final.
Por isso, a comparação integral usa a PR 258; não se afirma que a suíte
completa terminou na PR 259. Nos demais workflows, as contrapartes da PR 259
foram verificadas diretamente, sem novos IDs de falha. Variações de revisão
0068 → 0069 em asserts antigos e identificadores sintéticos foram distinguidas
de novas causas.

O [workflow de Dispositivos 42027091897](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42027091897)
passou nos seis jobs; seu PostgreSQL voltou a 54 aprovados, encerrando a
regressão de permissão encontrada na primeira rodada. O registro sanitizado
`stage8-general-ci-round2-final.json` e suas notas discriminam as contrapartes,
os 121 registros resolvidos e a limitação da base de comparação.

## Publicação e saúde do ambiente

A verificação somente leitura da Railway terminou em **2026-11-05 às
12:51:50 UTC**. Os três serviços de aplicação confirmaram o commit
`da3a0086a038f0f84024cc38c5960d3a2935ffbe`:

| Serviço | Deployment | Estado final |
|---|---|---|
| API | `cadda613-ff9c-4530-b4c5-10ebbade1fef` | SUCCESS, online, 1/1 réplica |
| Worker | `0004e1d9-627c-486a-a993-3722492e0054` | SUCCESS, online, 1/1 réplica |
| Ponte WhatsApp | `2e38616d-7e1e-4f18-ab8a-c874df8e8023` | SUCCESS, online, 1/1 réplica |

Os cinco serviços, incluindo Redis e PostgreSQL, estavam online com 1/1
réplica, zero réplicas em crash, zero avisos, zero alertas críticos e zero
falhas recentes na janela consultada de uma hora. `pendingWork` estava vazio.
Os deployments anteriores dos três serviços de aplicação já haviam sido
retirados na consulta final.

| Evento observado | Horário UTC em 2026-11-05 |
|---|---|
| `private_backup_archive_verified` | 12:46:42.192 |
| Início da migração 0068 → `0069_subscription_schedules` | 12:46:42.269 |
| Esquema validado após a migração | 12:46:43.212 |
| Barreira da API: `ok=true`, `status=subscription_scheduled_messages_schema_ready`, marcador 0069 | 12:46:49.593 |
| `Application startup complete` | 12:46:53.130 |
| Ponte: serviço privado iniciado | 12:45:59.132 |
| Ponte: `Orion QR: dispositivo conectado` | 12:45:59.814 |

O registro da barreira da API é JSON estruturado, localizado pelo filtro
`@status:subscription_scheduled_messages_schema_ready`. O código publicado
executa essa verificação com falha bloqueante antes de iniciar o Uvicorn.
A sonda exige `app_runtime`, `default_transaction_read_only=on` e
`search_path=pg_catalog`.

**Evidência do worker:** a chamada importada da barreira não apresentou um
marcador exclusivo nos logs observados. A confirmação é uma inferência
explícita apoiada no código exato publicado e na execução posterior: o
`on_startup` aguarda `wait_for_scheduled_messages` antes de retornar, e os
logs mostram tarefas normais concluídas, incluindo
`cron:scan_subscription_chatbot` às 12:50:07 UTC com resultado 0. Não se
atribui um horário de log inexistente à conclusão individual da barreira.

Os filtros restritos, da criação dos deployments às 12:45:13 UTC até a
conferência de falhas às 12:50:47 UTC, não encontraram traceback, falha de
startup/barreira, papel de banco inválido, erro genérico da ponte, encerramento
de conexão ou retomada pendente. Mensagens normais de Uvicorn e ARQ escritas
em stderr foram interpretadas pelo conteúdo; a marca `severity=error` do
transporte não foi contada como falha quando o conteúdo indicava conclusão.

O marcador do backup comprova a verificação feita pela publicação; esta
conferência não acessou seu conteúdo nem expôs a chave do objeto privado.
As evidências sanitizadas estão em `stage8-production-verification.json` e
`stage8-production-verification.md`. A verificação não executou CLI/terminal
remoto, consultas diretas ao banco, leitura de segredos, mutações na Railway
ou mensagens de teste.

## Conferência no Chrome conectado

A conferência ao vivo foi concluída em 2026-11-05, por volta de 12:56 UTC,
na sessão autorizada do usuário. Foram observados:

- Lista de Programadas carregada e seus quatro contadores em zero.
- Formulário de criação aberto, com o dispositivo existente disponível.
- Data padrão de amanhã, horário 14:00 e fuso da empresa
  `America/Sao_Paulo`.
- Intervalos de 10 a 60 segundos, com 60 selecionado; repetição com padrão
  “Não”; campo de texto com `{name}` e limite de 1.000 caracteres.
- Alternância para Template abrindo o seletor da biblioteca. A biblioteca
  vazia apresentou orientação para cadastrar um template.
- Retorno à lista sem salvar, revisar, confirmar ou agendar uma mensagem.
- Console sem avisos nem erros na conferência (`warn/error=[]`).

Foram registradas as capturas sanitizadas
`evidence/orion-programadas-formulario-producao-20261105.png` e
`evidence/orion-programadas-producao-20261105.png`.

O resultado qualifica os fluxos observados e o contrato explícito do Orion.
A lista vazia do BotBot não permitiu observar o processamento real, a
recorrência, as ações sobre ocorrências existentes ou o alcance real do botão
“Enviar Pendentes Agora”. Esses comportamentos têm implementação e testes
próprios documentados, sem alegação de paridade empírica integral com partes
privadas não observadas. Nenhuma mensagem real foi enviada durante a
observação ou a validação desta etapa, e nenhum complemento pago foi adquirido.

## Acompanhamento editorial

A [PR 261](https://github.com/QGBALTIGO/orion-assistente/pull/261) alterou somente
uma frase do card Fila para “Aguardando o envio ou enviando”, além deste registro
e do README. A substituição exata foi revisada; sintaxe e 45 testes DOM passaram.
O merge `149c43fba44b3f93bdcd90920751d79dc231d32a` conservou a árvore revisada
`36391408683510b62029c94077c85002a98c349e`. Essa validação focada não é
apresentada como nova execução integral da qualificação da PR 260.

Às 13:01:40 UTC, API (`84cb3085-ae01-46bc-93b4-22063bc389fd`) e worker
(`04e9ad34-081e-404f-b08f-2006ce8ed188`) estavam SUCCESS, online 1/1 na
revisão 149c43f. A ponte conservou a revisão da3a008 saudável: seu deployment
novo foi SKIPPED pelo filtro de caminhos, sem mudança em arquivos da ponte.
Os cinco serviços estavam saudáveis, sem alertas nem trabalho pendente.

O Chrome foi recarregado: texto novo, lista e contadores apareceram corretamente,
e o console permaneceu sem avisos ou erros. A etapa 08 foi encerrada nessa
conferência; a etapa 09 começou somente depois. Nenhum envio foi criado.
