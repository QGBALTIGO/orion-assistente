# WhatsApp para Serviço de assinaturas

## Objetivo e ordem de entrega

Reproduzir os comportamentos observáveis dos módulos solicitados do BotBot,
com componentes, tipografia, cores e navegação do Orion. A autorização é
restrita ao nicho `servico_assinaturas`. Os demais nichos e o conector global
da administração preservam suas regras.

Cada módulo deve ser observado, implementado, validado e publicado antes de
começar a investigação do próximo. Dependências internas pertencem à entrega
do módulo que as necessita; uma opção gravada sem efeito não é uma função
concluída. Não apresentar equivalência integral quando há comportamento ainda
não observado ou testado.

| Ordem | Módulo | Estado |
|---|---|---|
| 01 | Dispositivos | Publicado — PR 248, produção `976fa7a095b053e5589bab52eb058187ebba858c` |
| 02 | Chatbot | Observado em 2026-11-04; implementação em andamento |
| 03 | Testar Chatbot | Não iniciado |
| 04 | Templates | Não iniciado |
| 05 | Mensagens Enviadas | Não iniciado |
| 06 | Diagnósticos | Não iniciado |
| 07 | Campanhas | Não iniciado |
| 08 | Mensagens Programadas | Não iniciado |
| 09 | Mensagem em Massa | Não iniciado |
| 10 | Atribuições de Chat | Não iniciado |
| 11 | Enviar Mensagem | Não iniciado |
| 12 | Transcrição de Áudio | Não iniciado |
| 13 | Aquecimento | Não iniciado |
| 14 | Evitar Banimento | Não iniciado |
| 15 | Resolver Banimento | Não iniciado |
| 16 | Extrator de contatos de grupos | Não iniciado |
| 17 | Adicionar contatos ao grupo | Não iniciado |
| 18 | Verificador de números WhatsApp | Não iniciado |
| 19 | Link e QR do WhatsApp | Não iniciado |

## Método e fontes

O registro detalhado da etapa 02 está em [chatbot-observation.md](chatbot-observation.md).

Observação feita no Chrome conectado do usuário em 2026-11-03
(America/Cuiaba), pela extensão autorizada. Nenhum envio, reinício, exclusão,
desconexão ou salvamento de configurações foi executado no BotBot.

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
