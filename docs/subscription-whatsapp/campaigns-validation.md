# Etapa 07 — evidências de Campanhas

Estado em 2026-11-05: **publicado pela PR258 e conferido ao vivo no Chrome**.
Os seis gates da árvore publicada somam 3.110 execuções aprovadas. Backup,
catálogo 0068, startup e deployments foram verificados. A conferência no painel
publicado ocorreu das 06:50 às 06:53, em `America/Cuiaba`, sem salvar grupos ou
campanhas, agendar ou enviar mensagens reais.

A produção posterior da PR259 está preservada. A repetição específica do seu
WebKit terminou com sucesso às 07:02:04 (`America/Cuiaba`), com 74 testes
aprovados na mesma árvore da produção atual. **A etapa 07 está encerrada;
Mensagens Programadas passa à observação.** As falhas e exigências das revisões
intermediárias abaixo permanecem como histórico, sem substituir o resultado
da árvore efetivamente publicada.

## Base preservada

A base inicial era a publicação de Diagnósticos, commit
`81cf127c51c3fae015568d16d79724ebe0f00db8`, árvore
`46d4109b71afa2c986c6580ac994c0113725a17f`. Durante a implementação, a PR257
publicou o transporte compartilhado no commit
`5699297d4b873a411275c767109bab77c1afc5b6`, árvore
`bd4915991953ead3510db7eed4716edfe9cefbd0`.

As 48 alterações publicadas foram incorporadas. Quarenta e sete arquivos ficaram
idênticos à produção; `channel-manager.mjs` preserva suas alterações publicadas
e acrescenta somente o adaptador de arquivos privados de Campanhas. A base e o
índice locais foram alinhados ao commit real 5699297. PRs abertas não integradas
à produção não foram incluídas.

## Revisão e correções antes do CI

- A revisão de audiência conta os telefones distintos antes da materialização;
  a consulta retorna no máximo 10.001 linhas deduplicadas. Uma combinação de
  grupos não carrega centenas de milhares de associações na aplicação.
- Datas próximas ao limite do calendário e janelas que atravessam meia-noite
  produzem validação explícita, incluindo inexistência de próxima janela.
- A lista real de fusos revelou `Factory` e `localtime`, incompatíveis com Intl.
  Essas pseudozonas são recusadas, opções não suportadas pelo navegador são
  filtradas e um fuso já salvo nunca é substituído silenciosamente.
- A expansão de marcadores tem orçamento agregado incremental de 64 MiB,
  verificado tanto na revisão quanto na confirmação, antes de gravar audiência.
- Remoção de contato e revogação invalidam pendências no mesmo bloqueio do tenant.
  Reintroduzir um contato no grupo não reativa uma campanha anterior.
  O marcador persiste também depois do claim e durante pausa/retomada, sem
  modificar o ledger; um recibo tardio conserva a verdade do transporte.
- O detalhe confirmado usa o resumo completo dos grupos congelados, incluindo
  quantidade de bloqueados e estado de arquivamento, compatível com o controller.
- A transcrição já autenticada registra a conclusão do ASR antes de processar
  opt-out do evento de áudio original, tudo na mesma transação. Não há chamada
  adicional ao provedor de transcrição.
- A ponte usa a recuperação de mensagens publicada na PR257: somente ID,
  destinatário e direção corretos recuperam o protocolo cifrado. Um ID sozinho
  é recusado também nos testes novos de Campanhas.

## Evidência local

| Conjunto | Resultado observado | Limite da evidência |
| --- | --- | --- |
| Ponte, gate dedicado sobre 5699297 | 411 aprovados, zero falhas/skips | Sockets e provedores sintéticos; SDK nativo e armazenamento cifrado reais. |
| Permissões, rotas e histórico | 167 aprovados | Campanhas restrita a assinaturas e sem reenvio genérico no histórico. |
| Python, gate dedicado local daquela revisão | 1.743 aprovados, seis avisos de depreciação; 58,79 s | Inclui as regressões de orçamento, fuso, revogação após claim e contrato do detalhe daquela fase. |
| Backend funcional de Campanhas | 137 aprovados | Conjunto incluído no gate Python local daquela revisão. |
| DOM completo com espaçamento do menu | 344 aprovados | Inclui 52 casos de Campanhas; 17,60 s. |
| DOM de Campanhas após alinhamento final da fixture | 52 aprovados | Primeira página do detalhe e três destinatários coerentes com a API; 14,05 s. |
| Smoke HTTP em loopback | 8 aprovados | Schemas reais; sete operações, uma confirmação sintética e nenhum efeito de provedor. |
| Coleta de navegador | 32 casos, 16 por engine | Coleta e compilação; execução de Chrome oficial/WebKit fica no CI. |
| Coleta PostgreSQL funcional | 32 casos | Cookies/CSRF, papéis SQL e broker reais; execução fica no CI. |
| Release local | 311 aprovados | Backup, barreiras, sanitização e compatibilidade, documentados separadamente. |

Naquela fase, a execução no banco descartável dos testes funcionais e dos 51
testes de catálogo da release, com os três papéis reais, ainda era necessária.
Essa execução foi concluída no CI e está discriminada no resultado final.
Todos os envios, contatos, recibos, áudios e objetos de armazenamento usados na
qualificação são sintéticos. Os testes de navegador geram capturas de celular,
tablet e desktop, claro/escuro, incluindo revisão e recuperação após perda da
resposta.

## Primeira execução no CI: bloqueios encontrados e corrigidos

A PR258 foi aberta com head `da7280fc334bed8cc26e53c8021839d0662210f2`,
árvore `2c8e4043e88b6bd8d5d32d8caaada61789b72646`. O checkout dos testes foi
`023b94e55d099544776bda2d584271c004b810bd`, merge sintético com a produção
5699297 e a mesma árvore. Essa revisão **não foi qualificada para publicação**.

| Gate dedicado, execução 41927000238 | Resultado final |
| --- | --- |
| Python | 1.743 aprovados, seis avisos |
| Ponte | 411 aprovados, zero falhas/skips |
| DOM | 342 aprovados, zero falhas/skips |
| PostgreSQL | 312 aprovados e 25 falhas; migração e 51 casos de catálogo aprovados |
| Chrome | 60 aprovados e uma falha; execução interrompida no primeiro erro |
| WebKit | 60 aprovados e uma falha; execução interrompida no primeiro erro |

Os 25 traces do PostgreSQL identificam duas consultas: vinte falhas na projeção
de destinatários e cinco no controle de intervalo usado por polls anteriores.
O parâmetro sem tipo em `:now - interval` era inferido como intervalo, impedindo
a comparação com timestamps. Quatro expressões passam a usar
`CAST(:now AS timestamptz)`. Uma regressão funcional adicional prepara essas
consultas com audiência vazia e verifica o poll ordinário e a leitura de rascunho.
Não houve alteração de schema ou de permissões para resolver essa falha.

No Chrome, um debounce de pesquisa ainda pendente invalidava a leitura de
detalhe iniciada por uma ação explícita. Três testes com timer controlado
reproduziram a perda da revisão, antes de qualquer confirmação; o controller
cancela esse timer ao iniciar a leitura ou ação explícita. No WebKit, o mesmo
cenário chegou a uma etapa posterior: revogar acesso enquanto a carga passiva
do detalhe ainda estava em curso removia corretamente a rota antes do clique
do teste. O cenário passa a aguardar o estado observável de carga concluída
antes de revogar, mantendo a resposta 403, a remoção do menu e a ausência de
novas escritas, confirmações e efeitos. Os timeouts não foram aumentados.

A inspeção de 36 capturas do Chrome, em três tamanhos e dois temas, encontrou
abas de destinatários com aparência nativa e palavras quebradas no celular e
tablet. O componente passa a usar as abas existentes do Orion, com quebra
entre botões e rótulos inteiros; o cenário visual mede a quantidade de linhas
de cada rótulo, a altura e a contenção. As capturas posteriores conferiram o ajuste.
Depois das correções, passaram 137 unitários do backend e 55 testes DOM de
Campanhas. Foram coletados 33 casos funcionais de PostgreSQL e 32 de navegador,
sem declarar essa coleta como execução. A revisão independente dos quatro
casts e do cancelamento do timer não encontrou novo bloqueio.
Os resultados finais da nova árvore, registrados abaixo, sustentam a conclusão
de prontidão; os erros desta primeira execução ficam preservados como evidência.

## Atualização concorrente da produção e composição da inicialização

A revisão corrigida `427b0cff44d2432279f8f80407eb803f386587ea`, árvore
`c14036ae5a96f86e9c27383f8a0c9f7cc43c77b8`, executou 1.743 testes Python,
345 DOM e **338 PostgreSQL aprovados**. Estes últimos incluem 33 funcionais de
Campanhas, 51 de catálogo e 254 regressões anteriores. As 25 falhas SQL foram
resolvidas, com execução das etapas antes bloqueadas pela preparação das
consultas. O checkout foi `311dcf5bb4e42f77020a0d2656a48524fb986d5e`.

Durante essa rodada, a produção avançou diretamente para
`01d99bb0341bc4f3d91910930a4f5c64d263421b`, árvore
`57fb69f63939471e83a342c03502729f3c7bfd1f`. Esse commit acrescenta a conexão
opcional por SSH com a Hostinger e preserva a porta existente. Seus oito
arquivos publicados foram incorporados; a PR255 permanece aberta e não foi
tratada como merge. A qualificação final precisou incorporar essa nova base.

A composição mantém o `execve` que remove as credenciais temporárias antes de
criar qualquer subprocesso. No modo normal, a presença de uma dessas chaves
provoca uma única reexecução limpa do launcher. No modo SSH, o launcher entra
diretamente no supervisor sanitizado. Ambos executam a checagem somente leitura
de 0068 antes da API; o supervisor também exclui as chaves SSH do ambiente da
checagem e só inicia API/túnel após sucesso. Timeout, erro e impossibilidade de
iniciar a checagem impedem a supervisão. Pinning, argumentos de rede, sinais,
reconexão, workers e lifecycle publicados permanecem preservados.

Passaram **115 testes focados** de inicialização, proxy e release nessa
composição. A revisão independente comparou as funções do supervisor com a
produção e confirmou que somente sua entrada foi composta com a barreira.

O WebKit da revisão intermediária também revelou uma corrida no teste de
resposta 409: a tela indica operação pendente antes de o POST chegar à fixture.
O teste passa a aguardar o POST exato e sua resposta `409/CAMPAIGN_CONFLICT`,
além do fim da carga, antes de ler o ledger. Conserva a mesma chave, uma única
recuperação por GET e a igualdade de todas as escritas. Os timeouts permanecem
iguais. As capturas dessa revisão confirmam as abas corrigidas e a recuperação;
a árvore integrada recebeu seus próprios logs e capturas na qualificação final.

## Critério de publicação

Exigir os seis gates dedicados e comparar cada workflow geral executado com a
árvore de produção atual, usando os logs finais, o checkout, a fase e a contagem
das falhas. Falhas históricas permanecem explícitas. Um job herdado não é
declarado aprovado, e uma falha nova não é dispensada por outra suíte passar.

Depois do merge da árvore qualificada, verificar backup e catálogo 0068, startup
da API e do worker, deploy dos três serviços no mesmo commit e o formulário no
painel publicado. Consultas ao vivo não criam grupos, campanhas ou envios reais.
A observação de Mensagens Programadas só começa depois dessa conclusão.

## Revisão de continuidade — limites numéricos da API

A retomada conferiu a árvore `c14036ae5a96f86e9c27383f8a0c9f7cc43c77b8`
da PR 258 e encontrou dois limites de entrada ausentes. A versão de autorização
de contato podia exceder o `bigint` usado no SQL. A página das listas também
podia produzir um `OFFSET` fora desse domínio. Esses valores inválidos podiam
chegar ao banco e causar erro interno; não foi executada nenhuma requisição
desse tipo em produção.

A versão agora aceita de zero até `2**63-1`. As duas rotas de listagem aceitam
páginas de 1 a 10.000, seguindo o limite já publicado de Chatbot e Mensagens
Enviadas. A paginação inválida é rejeitada antes de abrir a transação; a versão
inválida é rejeitada após a autorização inicial e antes da mutação. Seis casos
HTTP demonstram a resposta 422, e dois preservam as extremidades válidas da versão. Os seis casos de
rejeição falharam antes da correção. Após o ajuste, **182 testes de API,
política, runtime e integração passaram em 1,37 s**, sem envios externos.

Esta correção foi incluída na qualificação do novo commit; os resultados da
árvore anterior não são apresentados como testes executados da nova revisão.

## Segunda atualização da produção — transporte HTTPS opcional

A revisão `96243421789550418c33bc70f25deef45676f69a`, árvore
`4780eaa99d48d468d95cfb4592fa5c4dd7220969`, executou **1.825 testes Python,
338 PostgreSQL, 345 DOM e 74 Chrome aprovados**. O checkout observado foi
`7532442daeb45aae58762f63547154f2d1664c2f`, sobre `01d99bb`. Esses resultados
são intermediários: durante a execução, a produção avançou para
`a2f84ea6781a08ad8a97baa5ffce951ae60f3bdc`, árvore
`8e6ce62d8f3b566ee57584ed52482552e5112962`.

Os nove arquivos publicados dessa atualização foram incorporados. O transporte
HTTPS opcional e autenticado conserva os pins de SSH, o destino TCP fixo e a
supervisão de API, SSH e HTTPS. O download da imagem segue somente redirects
HTTPS e verifica os hashes publicados do arquivo comprimido e do executável
antes de executá-lo. A composição de Campanhas preserva a fronteira limpa de
`execve` e o probe de catálogo antes de qualquer processo de serviço. As cinco
variáveis de transporte são excluídas tanto do probe quanto da API.

A comparação de AST confirmou que todas as funções e classes do supervisor
correspondem à produção `a2f84ea`, exceto `main`, que conserva a barreira de
catálogo. Dockerfile, documentação de ingress, configurações Hostinger e o
novo arquivo de testes HTTPS têm os mesmos blobs da produção. Passaram
**158 testes focados em 0,91 s**, sem falhas ou skips. Os testes de transporte
usam processos, sinais, relógio e saúde sintéticos; não abrem conexão externa.
O workflow dedicado inclui `test_https_transport.py` nos paths e no comando
Python. A nova árvore executou os seis gates e a comparação geral sobre essa
base antes da publicação, conforme o registro final abaixo.

## Qualificação final da PR258

O head integrado `e5afccda2880d37121ab9467c36bfefe7095c5a4` e o checkout
sintético `e2269ecac14e7970706ba586be1acfd1f612cba5` têm a mesma árvore
`503dcbec9eb05252460ed545acb64f1901061cd1`. Os seis gates do
[run 41934922779](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41934922779)
executaram esse checkout e concluíram com sucesso:

| Gate | Resultado final |
| --- | --- |
| Python | 1.868 aprovados, zero falhas/skips e seis avisos |
| PostgreSQL | 338 aprovados: 33 funcionais de Campanhas, 51 de release e 254 anteriores |
| Ponte | 411 aprovados, zero falhas/skips |
| DOM | 345 aprovados, zero falhas/skips |
| Chrome oficial | 74 aprovados, incluindo 16 de Campanhas |
| WebKit | 74 aprovados, incluindo 16 de Campanhas |

São **3.110 execuções aprovadas**. Foram conferidas 88 capturas, 44 por
navegador, nos três tamanhos e dois temas, incluindo oito estados de recuperação
por navegador. Imagens idênticas às já revisadas foram comparadas por SHA-256;
as diferenças atuais foram reinspecionadas, sem novo bloqueador visual.

A comparação geral fechou **64 de 64 jobs lógicos**, sem regressão nova
identificada: 50 aprovados, 11 com falhas históricas de testes e três com causas
históricas verificadas fora desses resumos. A suíte ampla registrou 8.740
aprovados, 169 falhas, 43 erros e dez skips. As 212 ocorrências de falha/erro e as
19 falhas Node coincidem com o baseline em identificador, fase e multiplicidade.
As três causas anteriores fora desses resumos foram duas asserções obsoletas
da revisão 0058 e o timeout de `.op-payment-body` em Planos/Chrome. **A suíte
geral não é declarada verde.**

O baseline `a2f84ea` contém 68 logs atuais finais e duas referências explícitas
de `01d99bb`, para Cakto e predeploy 0056, cujos 883 blobs de entrada relevantes
e comandos são idênticos. Esses dois resultados não são apresentados como
execuções em `a2f84ea`; a igualdade do ambiente externo não foi demonstrada.
O candidato executou os próprios jobs correspondentes, com 89 e cinco testes
aprovados.

Agenda WebKit passou com 40 testes, zero falhas/skips, em 85,91 segundos na
tentativa 2 do
[run 41934922623](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41934922623),
job `126957316593`, no checkout exato `e2269eca`. A primeira tentativa foi
cancelada durante a instalação APT, antes de pytest. Somente esse job foi
repetido; os quatro outros IDs da tentativa 2 reutilizam os resultados
anteriores e não contam como novas execuções. A verificação ASR sintética de
WAV/OGG e seus quatro testes também passaram. O detalhamento consolidado está
na [PR258](https://github.com/QGBALTIGO/orion-assistente/pull/258).

## Publicação e conferência no painel

A PR258 foi integrada em **2026-11-04, às 20:28:29 (`America/Cuiaba`)**, no
commit `23b78bf42086766694a992474f078c2a4ed0a2eb`. Sua árvore
`503dcbec9eb05252460ed545acb64f1901061cd1` é a mesma qualificada no CI; os pais
são a produção `a2f84ea` e o candidato `e5afccd`. API, worker e ponte concluíram
o deploy desse commit. Backup privado verificado, migração 0067 → 0068,
catálogo e barreiras de startup estão discriminados no
[registro de publicação](campaigns-release-validation.md).

Em **2026-11-05, das 06:50 às 06:53 (`America/Cuiaba`)**, o Chrome conectado
do usuário abriu o painel publicado da empresa de assinaturas. Foram conferidos:

- Menu de assinaturas e lista vazia de Campanhas.
- Formulário de campanha com dispositivo, texto, template, agendamento, fuso,
  dias da semana e exigência de grupos.
- Alternância entre texto e template e carregamento do seletor da biblioteca.
- Lista de grupos e formulário de novo grupo, sem salvar.
- Retorno à lista original, com nenhum aviso ou erro no console capturado.

As capturas da lista e do formulário foram registradas fora do repositório.
Na continuidade da conferência, a captura completa do formulário publicado foi
inspecionada visualmente, sem cortes ou elementos sobrepostos identificados;
a tela retornou à lista sem salvar. Essa conferência foi da interface publicada:
não criou campanha ou grupo, não agendou e não enviou mensagem real. Ela não é
apresentada como teste de entrega no WhatsApp.

## Base posterior PR259 e encerramento da etapa

A produção avançou para `19fbe9a98cc59dbc927f69517666f77ec2eb8d6b`, árvore
`6ffd7381c77fcadfa2d64af65d91d5ded4b0eb6a`, pela
[PR259](https://github.com/QGBALTIGO/orion-assistente/pull/259). Seu primeiro
pai é o merge da PR258; os arquivos próprios de Campanhas permaneceram iguais.
Em 2026-11-05, às 06:55:51 (`America/Cuiaba`), os três deployments da nova base
estavam em `SUCCESS`/Online, com uma réplica ativa cada e nenhum alerta
observado. Os identificadores estão no registro de publicação.

A nova base registrou erro de página capturado por `pageerror` em
`/api/v1/account/business-identity` durante a troca de perfil do cenário
`test_filters_expiry_revocation_empty_and_other_niche_do_not_admit[webkit]`.
A repetição da tentativa 2 foi cancelada. A falha inicial e o cancelamento
permanecem registrados, e a causa da falha não foi comprovada.

A tentativa 3 do
[run 41950107314](https://github.com/QGBALTIGO/orion-assistente/actions/runs/41950107314),
job `127201760767`, terminou em **SUCCESS em 2026-11-05 às 07:02:04
(`America/Cuiaba`)**. Os logs registram **74 aprovados e 74 deselecionados em
392,13 segundos**; os deselecionados não são contados como execuções. O checkout
foi `0a59ddc7a67a0283e48aed6219ddfd00479b9616`, árvore
`6ffd7381c77fcadfa2d64af65d91d5ded4b0eb6a`, idêntica à produção `19fbe9a`.
Somente o job WebKit foi repetido: os outros cinco jobs mantêm os resultados
anteriores reaproveitados, sem novas execuções atribuídas a essa tentativa.

Com a qualificação original, a publicação e a conferência ao vivo verificadas,
e o WebKit da base posterior aprovado na árvore atual, **a etapa 07 foi
encerrada**. A observação de Mensagens Programadas começa depois dessa
confirmação. O histórico da falha anterior permanece preservado com sua causa
não comprovada; a conclusão não declara paridade dos fluxos inacessíveis do
BotBot nem execução real de envio.

## Limites da referência e decisão de custo

A conta BotBot permite observar lista, formulário e detalhe de Campanhas,
mas a conclusão dos fluxos de criação/edição exige complemento pago. O usuário
recusou a compra: **não comprar nem ativar complementos pagos e não solicitar
novamente autorização para essa compra**. O trabalho continua com o acesso
disponível, sem contornar a restrição.

Os comportamentos privados bloqueados continuam não observados. Os testes
sintéticos do Orion validam o contrato implementado, sem demonstrar equivalência
de execução com esses fluxos. Não se declara paridade integral; o limite consta
também no [registro de observação](campaigns-observation.md).
