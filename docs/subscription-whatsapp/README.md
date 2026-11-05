# WhatsApp para Serviço de assinaturas

## Objetivo e ordem de entrega

Reproduzir os comportamentos observáveis dos módulos solicitados do BotBot,
com componentes, tipografia, cores e navegação do Orion. A autorização é
restrita ao nicho `servico_assinaturas`. Os demais nichos e o conector global
da administração preservam suas regras.

Os módulos acessíveis devem ser observados, implementados, validados e
publicados um de cada vez. Em 2026-11-05, o usuário autorizou explicitamente
manter Atribuições de Chat pendente e continuar até o fim dos módulos
acessíveis, sem contratar acesso pago. Bloqueios de plano devem ser
registrados como pendências e não exigem nova confirmação para prosseguir.
Essa autorização permite avançar na sequência; não representa conclusão ou
implementação de uma função bloqueada.

Dependências internas pertencem à entrega do módulo que as necessita; uma
opção gravada sem efeito não é uma função concluída. Não apresentar
equivalência integral quando há comportamento ainda não observado ou testado.

| Ordem | Módulo | Estado |
|---|---|---|
| 01 | Dispositivos | Publicado — PR 248, produção `976fa7a095b053e5589bab52eb058187ebba858c` |
| 02 | Chatbot | Publicado — PR 249, produção `112e92ea79240a1980f484bf2787c4400bd5418d` |
| 03 | Testar Chatbot | Publicado — PR 250, produção `8146d5f7f50bb778d188f74b7e52452394870c50` |
| 04 | Templates | Publicado e conferido ao vivo em 2026-11-04 — PR 251, produção `7164ce5ca9e4c795910ca5255f510f625dad0a11` |
| 05 | Mensagens Enviadas | Publicado e conferido ao vivo em 2026-11-04 — PR 252, produção `f58730ac8e380bd2c12286c69c1db3873d3cbf0f` |
| 06 | Diagnósticos | Publicado e conferido ao vivo em 2026-11-04 — PR 253, produção `81cf127c51c3fae015568d16d79724ebe0f00db8` |
| 07 | Campanhas | Publicado e conferido ao vivo em 2026-11-05 — PR 258, produção `23b78bf42086766694a992474f078c2a4ed0a2eb`; etapa encerrada após aprovação WebKit da base posterior PR 259, conforme o [registro de validação](campaigns-validation.md) |
| 08 | Mensagens Programadas | Publicado e conferido ao vivo em 2026-11-05 — PR 260, produção `da3a0086a038f0f84024cc38c5960d3a2935ffbe`; etapa encerrada com ajuste editorial da PR 261 (`149c43fba44b3f93bdcd90920751d79dc231d32a`), conforme o [registro de validação](scheduled-messages-validation.md) |
| 09 | Mensagem em Massa | Publicado e conferido ao vivo em 2026-11-05 — PR 262, produção `d17cb67a29fca8293299ac3b2b969601b540075a`; etapa encerrada, conforme o [registro de validação](bulk-messages-validation.md) |
| 10 | Atribuições de Chat | Pendente: plano sem acesso e criação retorna 403. Sem implementação ou publicação; continuidade dos módulos acessíveis autorizada pelo usuário. [Registro de observação](chat-assignments-observation.md) |
| 11 | Enviar Mensagem | Publicado e conferido ao vivo em 2026-11-05 — PR 263, produção `32a90c0fd003c5fbac70c1bd541bf9ce20171040`; etapa encerrada, conforme o [registro de validação](direct-messages-validation.md) |
| 12 | Transcrição de Áudio | Transcrição e métricas existentes preservadas; carteira e recargas pendentes, sem compra ou nova implementação. [Registro de observação](transcription-observation.md) |
| 13 | Aquecimento | Publicado e conferido ao vivo em 2026-11-05 — PR 264 e correção PR 265, produção `d2c1d279a1da03909785f54faac5ce6e1873bbcb`; etapa encerrada. [Observação](warmup-observation.md) · [Validação](warmup-validation.md) |
| 14 | Evitar Banimento | Publicado e conferido ao vivo em 2026-11-05 — PR 266, produção `f33b373ea0273296d0dc7b8e02c75353a9b648cc`; etapa encerrada. [Observação e validação](avoid-ban-observation.md) |
| 15 | Resolver Banimento | Publicado e conferido ao vivo em 2026-11-05 — PR 268, produção `784c31046420b406b655ee5913e39e102f971c76`; etapa encerrada. [Observação e validação](resolve-ban-observation.md) |
| 16 | Extrator de contatos de grupos | Publicado pela PR 269, produção `5633a2ec596f1f7884b17f6c685f296318697fe9`, com 4.902 execuções dedicadas aprovadas. A conferência real identificou disponibilidade incorreta do dispositivo desconectado; corretivo de estado validado de forma focada, aguardando publicação e nova conferência. [Observação e contrato](group-contacts-observation.md) |
| 17 | Adicionar contatos ao grupo | Não iniciado |
| 18 | Verificador de números WhatsApp | Não iniciado |
| 19 | Link e QR do WhatsApp | Não iniciado |

A etapa 04 passou nos seis grupos específicos de CI: 1.198 casos Python,
147 PostgreSQL, 309 da ponte, 226 DOM, 33 no Chrome oficial e 33 no WebKit.
A comparação das suítes gerais não encontrou novos IDs de falha ou erro em
relação à base; as falhas anteriores permanecem discriminadas no
[registro de validação](templates-validation.md). O merge tem a mesma árvore
testada, `ef30f3c3a5d0507c2f7dc25619bcb7f2b66d91ec`.

Templates foi encerrado após confirmação dos deployments de API, worker e
ponte na mesma revisão, backup privado verificado, migração 0063 → 0064 e
conferência no Chrome conectado da empresa BaltigoFlix. A biblioteca e os
cinco formulários foram inspecionados sem criar dados nem enviar mensagens
reais. Os identificadores dos deployments e os limites dessa conferência
estão no registro de validação. A observação de Mensagens Enviadas começou
após essa confirmação.

A etapa 05 passou nos seis grupos específicos: 1.262 casos Python, 186
PostgreSQL, 314 da ponte, 251 DOM, 41 no Chrome oficial e 41 no WebKit. Os 56
jobs dos 23 workflows gerais foram comparados à produção anterior, sem novos
IDs de falha nem diferenças de fase ou multiplicidade. O passivo histórico e
a repetição bem-sucedida de uma instalação interrompida estão discriminados
no [registro de validação de Mensagens Enviadas](sent-messages-validation.md).
O merge preserva a árvore testada, `28ac47dc292556b77b80fbc80607077265011a41`.

A etapa 05 foi encerrada após os três serviços em `SUCCESS`/Online,
backup privado verificado, migração 0064 → 0065 e conferência no Chrome
conectado até 18:18:08Z de 2026-11-04. A lista, o detalhe somente leitura, os
filtros e os totais globais foram conferidos sem reenvio real. O histórico de
áudio sem duração aparece como “Não informado”. A observação de Diagnósticos
começou depois dessa confirmação.

A etapa 08 foi publicada pela [PR 260](https://github.com/QGBALTIGO/orion-assistente/pull/260)
com a árvore qualificada `830a9a262bb4b69a826739cc7457185fc2f7af88`.
Os seis gates do [workflow dedicado 42027092137](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42027092137)
passaram: 2.257 casos Python, 439 PostgreSQL, 451 da ponte, 424 DOM,
91 no Chrome e 91 no WebKit, totalizando 3.753 execuções de teste.
O CI geral manteve 14 jobs com falhas históricas, sem novas identidades de
falha no comparativo realizado; o limite da comparação da suíte FATIA com a
PR 258, devido ao timeout da PR 259, está explícito no registro de validação.

API, worker e ponte foram conferidos no mesmo commit, em `SUCCESS` e online
com 1/1 réplica. Backup privado verificado, migração 0068 → 0069 e barreiras
de catálogo precederam a execução. A lista e o formulário foram conferidos
no Chrome conectado, incluindo os padrões de horário, intervalo, repetição,
texto e seletor de template, sem salvar, revisar, agendar ou enviar mensagens.
O ajuste isolado do texto de ajuda do card Fila foi publicado na PR 261,
com substituição exata revisada, sintaxe e 45 testes DOM aprovados. API e
worker confirmaram `149c43fba44b3f93bdcd90920751d79dc231d32a`; a ponte
permaneceu saudável na revisão funcional da PR 260, pois seus arquivos não
mudaram. A nova frase e a lista foram conferidas no Chrome, sem erros, antes
de iniciar a etapa 09.

A etapa 09 foi publicada pela [PR 262](https://github.com/QGBALTIGO/orion-assistente/pull/262),
preservando a árvore qualificada `505fd9e7b588dac70bd5c325b5c8d9e063aa47d1`.
Os seis gates do [workflow dedicado 42043961467](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42043961467)
passaram: 2.559 casos Python, 537 PostgreSQL, 485 DOM, 488 da ponte,
105 no Chrome e 105 no WebKit, totalizando 4.279 execuções de teste.
Nos 64 jobs gerais, houve 49 aprovações, 13 falhas anteriores comparadas,
um timeout de FATIA e um cancelamento durante a preparação de
transport-hardening. Essas duas execuções incompletas não comprovam uma
comparação funcional concluída; o repositório inteiro não está aprovado.
Os limites e as contrapartes estão no [registro de validação](bulk-messages-validation.md).

API, worker e ponte confirmaram o mesmo merge `d17cb67a29fca8293299ac3b2b969601b540075a`,
em `SUCCESS` e online com 1/1 réplica, sem alertas atuais. O backup privado
foi verificado antes da migração 0069 → 0070, e as barreiras da API e do
worker confirmaram `0070_subscription_bulk`. O primeiro build do worker
falhou no download do Chisel por HTTP 504; uma única reconstrução da mesma
origem, sem mudança de configuração, terminou com sucesso. Não ficaram
deployments pendentes nem alterações preparadas na Railway.

A conferência no Chrome conectado incluiu histórico, totais, bancada,
intervalo padrão de 10 segundos e limites de 10–60, abas Grupos/Contatos/Entrada
manual, alternância Texto/Template e captura no tema escuro do Orion. O
formulário foi fechado e o histórico voltou a ser exibido, sem avisos ou
erros capturados no console, sem criar ou confirmar lote e sem envio real.
A execução foi validada nos ambientes sintéticos; a conferência de produção
foi de leitura e preparação visual. A etapa 10 começou após esse encerramento.

A etapa 10 permanece pendente por bloqueio na referência. O BotBot exibe a lista com
aviso de indisponibilidade no plano e o link visível de criação retorna
HTTP 403. As fontes gratuitas acessíveis não esclarecem os campos, o destino
nem o efeito de uma atribuição. Os controles existentes no Orion foram
mapeados, mas não comprovam o comportamento da referência. Nenhum complemento
foi contratado. O usuário autorizou explicitamente deixar essa etapa pendente
e continuar, um módulo acessível por vez, até o final da lista, sem pagar nem
pedir nova confirmação por bloqueios de plano. A observação de Enviar Mensagem,
etapa 11, foi iniciada sob essa autorização. O [registro de observação](chat-assignments-observation.md)
preserva os fatos confirmados e as questões funcionais ainda desconhecidas.

A etapa 11 foi publicada pela [PR 263](https://github.com/QGBALTIGO/orion-assistente/pull/263),
com o merge `32a90c0fd003c5fbac70c1bd541bf9ce20171040` e a árvore qualificada
`14d352c68feb56da64f0f6995c579dc89a172148`. Os seis gates do
[workflow dedicado 42060576006](https://github.com/QGBALTIGO/orion-assistente/actions/runs/42060576006)
passaram na candidata final: 2.759 casos Python, 588 PostgreSQL, 560 DOM,
488 da ponte, 121 no Chrome e 121 no WebKit, totalizando **4.637 execuções**,
sem falhas ou testes ignorados. A primeira candidata encontrou perda tardia
de foco no WebKit; o módulo foi corrigido e a asserção original passou na
execução integrada final. Resultados de candidatas diferentes não foram somados.

O corte do CI geral às 16:14:57–16:15:01Z de 2026-11-05 continha 62 jobs:
46 aprovados, 13 com falhas herdadas comparadas e 3 ativos. No adendo terminal
às 16:26:43Z, havia 47 aprovados, as mesmas 13 falhas herdadas, um timeout de
FATIA/pytest (20 minutos, progresso de 13%, sem resumo final) e o cancelamento
do smoke FATIA/ASR, sem saída de inferência ou causa interna comprovada.
Essas execuções incompletas não comprovam aprovação
global. O [registro de validação](direct-messages-validation.md) preserva os
dois momentos, as contrapartes e os limites da comparação.

API e worker confirmaram o merge da etapa 11 em `SUCCESS`, online com 1/1
réplica. A ponte permaneceu na revisão `d17cb67a29fca8293299ac3b2b969601b540075a`,
também saudável: seu diretório tem a mesma árvore nas duas revisões e o novo
deployment foi ignorado pelos watchpaths. O backup privado foi verificado
antes do início da migração 0070 → 0071, e API e worker confirmaram
`0071_subscription_direct`. Os cinco serviços estavam online, sem alertas
ativos, operações pendentes ou alterações staged; não houve retry de build
nem alteração de configuração nessa publicação.

No Chrome conectado, `/painel?aba=enviar-mensagem` exibiu o novo menu e as
abas Texto/Mídia/Template. Foram conferidos os campos, limites, biblioteca
vazia e a exibição/ocultação do nome ao marcar Salvar como template.
A tela foi deixada em Texto com mensagem e número vazios e a opção
desmarcada; a leitura de erros retornou `errors=[]`. Não houve upload,
revisão, confirmação, criação de template, alteração de contato ou envio real.
A etapa 11 foi encerrada depois dessa conferência, preservando os limites
de observação e de qualificação documentados.

## Método e fontes

O registro detalhado da etapa 02 está em [chatbot-observation.md](chatbot-observation.md).
A qualificação técnica está em [chatbot-validation.md](chatbot-validation.md).
O registro da etapa 03 está em [chatbot-test-observation.md](chatbot-test-observation.md).
A qualificação da etapa 03 está em [chatbot-test-validation.md](chatbot-test-validation.md).
O registro da etapa 04 está em [templates-observation.md](templates-observation.md).
A qualificação da etapa 04 está em [templates-validation.md](templates-validation.md).
O registro da etapa 05 está em [sent-messages-observation.md](sent-messages-observation.md).
A qualificação da etapa 05 está em [sent-messages-validation.md](sent-messages-validation.md).
A observação da etapa 06 está em [diagnostics-observation.md](diagnostics-observation.md).
O contrato da etapa 06 está em [diagnostics-contract.md](diagnostics-contract.md).
A qualificação da etapa 06 está em [diagnostics-validation.md](diagnostics-validation.md).
A observação da etapa 08 está em [scheduled-messages-observation.md](scheduled-messages-observation.md).
O contrato da etapa 08 está em [scheduled-messages-contract.md](scheduled-messages-contract.md).
A qualificação e a conferência de produção da etapa 08 estão em [scheduled-messages-validation.md](scheduled-messages-validation.md).
A observação da etapa 09 está em [bulk-messages-observation.md](bulk-messages-observation.md).
O contrato da etapa 09 está em [bulk-messages-contract.md](bulk-messages-contract.md).
A qualificação e a conferência de produção da etapa 09 estão em [bulk-messages-validation.md](bulk-messages-validation.md).
A pendência, o mapeamento inicial e a autorização de continuidade da etapa 10 estão em [chat-assignments-observation.md](chat-assignments-observation.md).
A observação da etapa 11 está em [direct-messages-observation.md](direct-messages-observation.md).
O contrato da etapa 11 está em [direct-messages-contract.md](direct-messages-contract.md).
A qualificação, publicação e conferência ao vivo da etapa 11 estão em [direct-messages-validation.md](direct-messages-validation.md).
A observação, o mapeamento existente e a pendência da carteira da etapa 12 estão em [transcription-observation.md](transcription-observation.md).

Observação iniciada no Chrome conectado do usuário em 2026-11-03
(America/Cuiaba) e continuada em 2026-11-04, pela extensão autorizada.
Nas etapas 01–05, nenhum envio real, reinício, exclusão, desconexão ou
salvamento de configurações foi executado no BotBot.

Na etapa 06, a primeira seleção de um dispositivo em Diagnósticos iniciou
automaticamente uma mensagem de teste para o próprio número associado ao
dispositivo; depois, a interface informou sucesso. O botão “Enviar” não foi
acionado, e a página foi deixada após identificar esse comportamento. A
ocorrência e a decisão de exigir ação explícita no Orion estão no
[registro de observação de Diagnósticos](diagnostics-observation.md), sem
telefone ou conteúdo da mensagem. A conferência da etapa 05 permaneceu
somente leitura e sem envio real.

O repositório [morluto/rea](https://github.com/morluto/rea) foi examinado no
commit `c3647ade1e557c4179a8b825a37e0c2193cd2526`. Usamos seu método de
investigação por função: observação, estados, evidências, hipóteses,
implementação e comparação. O executável/MCP do REA não foi instalado nem
usado para controlar o navegador; a captura ocorreu pela extensão Browser.

Referências metodológicas: `docs/browser-observation.md`,
`docs/browser-scenario-contract.md` e
`.agents/skills/reverse-engineer-anything/references/evidence-workflows.md`
daquela árvore. Não atribuir às nossas anotações IDs de evidência gerados pelo
REA. Não copiar código, conteúdo privado ou identidade visual do BotBot.

## 01 — Dispositivos: evidências observadas

Fonte principal: `https://botbot.chat/user/device?channel=whatsapp`.
Os caminhos específicos de um dispositivo são registrados com `{device}`
para preservar os identificadores e dados da conta de referência.

| Evidência | Ação/estado observado | Resultado |
|---|---|---|
| devices-list | Abrir a lista | Card com nome, avatar, versão de conexão, telefone, estado e métricas de mensagens de hoje, 7 dias, 30 dias e total. |
| devices-status | Aguardar carregamento | Estado provisório de verificação muda para conectado; botões de recursos deixam o estado desabilitado de carregamento. |
| devices-actions | Abrir Opções | Conectar, alertas, configurações, diagnósticos, reiniciar e excluir. O card também tem atalhos de reinício/configuração/diagnóstico e estados de chatbot, transcrição e chamadas. |
| devices-create-limit | Abrir Criar Dispositivo | A conta está no limite 1/1. A criação retorna à lista com aviso de limite. O formulário de criação não pôde ser observado. |
| devices-connect | Abrir `/{device}/qr` | Tutorial em vídeo, confirmação de dispositivo conectado e ação explícita para atualizar a conexão. A atualização não foi acionada no número em uso. |
| devices-settings | Abrir `/{device}/edit` | Formulário descrito abaixo. Nenhuma configuração foi salva. |
| devices-call-windows | Abrir a seção condicional | O rótulo expande sete dias com início/fim, copiar entre dias/grupos, limpar e opção de feriados brasileiros. O checkbox foi restaurado antes de sair. |
| devices-delete | Abrir `/{device}/confirm-delete` | Confirmação de exclusão permanente das configurações/respostas, com alternativas de reconectar/editar e cancelamento. Cancelamos sem excluir. |

### Configurações observadas

- Nome do dispositivo e número opcional para testar a resposta fora do horário.
- Webhook: URL, ativação, envio de eventos por POST e descrição de desativação
  após falhas repetidas. Payload, tentativas e limiar não foram observados.
- Proxy: conexão compartilhada da plataforma ou URL HTTP do usuário, com
  credenciais. A oferta de proxy dedicado pertence ao serviço comercial da
  referência; o Orion não deve apresentar uma oferta que não fornece.
- Transcrição: ativação, duração de 1 a 30 minutos, modelo com `{name}`,
  `{transcript}` e `{footer}`, rodapé e opção de responder ao áudio.
  A descrição da última opção especifica procurar uma resposta para o conteúdo
  transcrito; ela não significa citar o áudio nem ligar/desligar o envio da
  transcrição. Sem regras cadastradas, não há resposta adicional correspondente.
- Chamadas: rejeição, mensagem após rejeitar, lista de exceções, janelas de
  recebimento por dia e opção de rejeitar em feriados nacionais brasileiros.
- Atendimento: fuso, início/fim e pausa por dia, copiar/limpar, tratamento de
  feriados, bloqueio de respostas fora do expediente e textos distintos para
  fora do horário e pausa. A interface descreve supressão de repetição por
  24 horas e exceção para o número de teste.

### Limites da evidência

Não executamos ações destrutivas, chamadas ou mensagens no número conectado.
Não observamos o formulário de criação, o pareamento de um aparelho novo,
erros reais de proxy/webhook nem o processamento de áudio pela referência.
Esses pontos precisam ser identificados como desenho/validação independente
do Orion ou obter evidência adicional antes de afirmar paridade completa.

O submenu **Alertas** do dispositivo foi aberto e identificado como monitor de
palavras-chave em grupos com envio para um bot do Telegram. Ele não é um log de
conexão e não pertence aos 19 módulos solicitados; não foi transformado em uma
função diferente com o mesmo nome. O diagnóstico completo continua reservado à
etapa 06; informações de estado da conexão pertencem à etapa 01.

## Base técnica do Orion

Produção inicial: `2037d8ec2b47ccba93412737d194d47e1c9fd181`, árvore
`5fde17a0a1b1d447e9b3cb3c5a247d9d52d70723`.

A API gerenciada por empresa (`qr-channels`) e `OrionChannels` oferecem
provisionamento, QR/código, estados e uma conexão canônica por empresa. O
conector `/api/v1/qr/*` é administrativo/global e não pode ser exposto aos
clientes. O novo módulo necessita autorização própria, distinta de conversas
e das capacidades futuras, e validação de propriedade/revisão em cada ação.

O limite inicial observado é uma conexão. A identidade lógica do dispositivo
deve permitir evolução sem obrigar os demais módulos a dependerem do conector
global. Métricas e estados devem vir de fatos reais; dados de teste ficam em
fixtures isoladas. Nenhuma lista de contatos ou configuração privada do
BotBot deve ser copiada para o repositório ou para empresas Orion.

### Decisões de implementação da etapa 01

- Rota do painel `dispositivos`, permissão exclusiva `devices.manage` e API
  `/api/v1/tenants/{tenant_id}/subscription-whatsapp/devices`. O singular
  `dispositivo` e a API global de administração preservam seu escopo anterior.
- Um dispositivo ativo por empresa, usando a conexão canônica existente.
  Criação não conecta nem liga automaticamente transcrição, webhook ou respostas.
  `device_id` e geração identificam o dispositivo; reiniciar preserva a sessão,
  e refazer a conexão troca a época das credenciais após encerramento explícito.
- Configuração versionada, operações identificáveis e resultados incertos
  conservadores. Um envio potencialmente executado não é repetido automaticamente.
  Cada parte de uma transcrição longa recebe identidade própria e aguarda a
  confirmação da parte anterior.
- Horários locais com início inclusivo e fim exclusivo; pausa dentro do
  expediente; sete dias sem duplicação; lista de exceções para chamadas. As
  janelas de chamadas permitem recebimento dentro delas e rejeitam fora delas.
  Telefone oculto não vira um número inventado: a oferta pode ser rejeitada sem
  enviar resposta ou testar uma exceção de telefone não verificado.
- Calendário de feriados brasileiros conforme a
  [publicação do MGI](https://agenciagov.ebc.com.br/noticias/202512/confira-o-calendario-oficial-de-feriados-nacionais-e-pontos-facultativos-em-2026):
  nove datas fixas e Sexta-Feira da Paixão. Pontos facultativos, Carnaval,
  Corpus Christi e datas municipais não são tratados como feriados nacionais.
  O algoritmo interno da referência não é visível; esta é a regra documentada
  do Orion para o campo observado.
- As configurações de atendimento, chamada e transcrição são independentes.
  O bloqueio fora do expediente afeta o mecanismo de respostas do chatbot;
  não apaga a política de rejeição de chamadas nem transforma a conversão de
  áudio em uma função do assistente legado.
- O proxy é HTTP, personalizado por dispositivo, com senha cifrada e sem
  retorno do segredo ao navegador. O Orion não anuncia venda ou fornecimento
  de proxy dedicado. Webhooks têm contrato Orion explícito, endereço público
  validado, destino de rede fixado para cada tentativa, nenhum redirecionamento
  e até três tentativas com o mesmo ID de evento. O payload e a contagem de
  tentativas da referência não foram observados.

### Áudio e limites operacionais

O caminho de áudio de assinaturas é separado do limite legado de dois minutos.
Aceita configuração de 1 a 30 minutos e arquivo de até 25 MB. O servidor decodifica
as amostras com FFmpeg, mede a duração e só então reserva o consumo e chama o
provedor. A saída normalizada remove metadados; arquivos temporários são
eliminados. Há uma tentativa de provedor por operação, prazo limitado,
rechecagem de autorização ao terminar e erro explícito em vez de texto cortado.
Textos de até 120 mil caracteres são divididos em mensagens ordenadas.

O contrato externo usado é a
[documentação oficial de transcrição da Groq](https://console.groq.com/docs/speech-to-text),
verificada em 2026-11-03. Os testes do provedor usam respostas sintéticas; um
áudio sintético de 30 minutos passou pelo decoder real local. Isso valida o
processamento e os limites, sem alegar medir a precisão do reconhecimento de
fala nem executar consumo pago nos testes.

Limites técnicos iniciais definidos para a publicação, independentes de planos
ou venda de créditos:

- `SUBSCRIPTION_DEVICE_ASR_DAILY_SECONDS=21600`: seis horas por empresa/dia UTC.
- `SUBSCRIPTION_DEVICE_ASR_GLOBAL_DAILY_SECONDS=86400`: 24 horas por projeto/dia UTC.
- Ausência dessas configurações desabilita consumo; apenas configurar o
  dispositivo não altera o limite nem inicia transcrição.

### Publicação e verificação

A revisão `0062_subscription_devices` é aditiva, sem copiar empresas, contatos
ou sessões. O pré-deploy faz backup privado com verificação do arquivo e leitura
de retorno antes da migração. As verificações de prontidão conferem proprietário,
RLS, permissões de colunas, índices exclusivos e chaves compostas sem ler dados
de cliente. O worker aguarda esse contrato usando a credencial restrita.

O workflow `subscription-devices.yml` valida Python, ponte Node, DOM, PostgreSQL
real com papéis separados e Chromium/WebKit em celular, tablet e desktop,
nos temas claro e escuro. Os cenários de navegação e provedor usam dados
sintéticos e impedem saídas externas. A publicação só será registrada nesta
seção após observar CI e os deployments concluídos da revisão exata.
