# Enviar Mensagem — observação da etapa 11

Data: 05/11/2026. Referência: [Enviar Mensagem do BotBot](https://botbot.chat/user/sent-text-message?channel=whatsapp), no Chrome conectado e autorizado.

A etapa 09 foi publicada e conferida antes desta observação. A etapa 10
permanece pendente pelo bloqueio do plano; o usuário autorizou continuar
pelos módulos acessíveis sem compras e sem nova confirmação por esse motivo.
O método continua a observação de estados, ações e resultados registrada
após o exame de `morluto/rea`; não se trata de uma saída do executável REA.

## Superfície acessível

A página apresenta “Enviar Mensagem Personalizada” e três abas: Texto,
Mídia e Template. Não houve bloqueio de plano nesta tela. Cada aba tem seu
próprio seletor de dispositivo e botão Enviar Mensagem.

| Aba | Campos e comportamentos observados |
|---|---|
| Texto | Dispositivo obrigatório; número com código do país ou contatos, com indicação de máximo de 10; busca de contatos por nome ou telefone; mensagem obrigatória com limite HTML de 1.000; ferramentas de formatação e emoji; opção Salvar como template. O campo de número tem `maxlength=100`, sem padrão HTML de validação. |
| Mídia | Dispositivo obrigatório, um número com código do país (`maxlength=15`), um arquivo, mensagem e Salvar como template. A ajuda informa máximo de 10 MB e extensões jpg, jpeg, png, webp, pdf, docx, xlsx, csv, txt, mp3, mp4, ogg, wav, opus e apk. |
| Template | Dispositivo; número com código do país ou até 10 contatos; busca de contatos; seleção de template existente. A biblioteca desta conta estava vazia, portanto não foi observada a seleção de um template concreto. |

Na aba Texto, a página avisa que as tags não funcionam nesse campo. Na aba
Mídia, avisa que o texto não acompanha áudio e que o texto enviado não tem
quebras de linha. Antes de selecionar um arquivo, a mensagem de mídia tem
`required` e `maxlength=1000`; não foi observado se a seleção de um tipo
específico altera essa validação.

## Interações exercitadas sem envio

- Ativar Salvar como template em Texto ou Mídia mostra Nome do template e
  muda o rótulo de Não para Sim. O nome não apresenta `required` nem
  `maxlength` no HTML observado.
- Com texto sintético selecionado, os botões aplicaram `*texto*`, `_texto_`,
  três crases antes/depois para monoespaço e `~texto~` para riscado.
- Emoji abriu um seletor com busca e categorias; escolher um emoji inseriu
  seu caractere no campo de mensagem. Uma resposta de controle demorou,
  mas a leitura seguinte confirmou que o seletor já havia aberto; o clique
  não foi repetido às cegas.
- Alternar abas preservou os campos do rascunho durante a visita. Recarregar
  a página descartou o texto sintético e desmarcou Salvar como template.
- A leitura final confirmou campos vazios e nenhum erro capturado no console.

## Limites da observação

Não foi acionado Enviar Mensagem, enviado arquivo, salvo template ou alterado
contato real. Não foram exercitados entrega, resposta perdida, falhas do
provedor, a seleção de dez contatos reais ou o envio de um template existente.
Não há evidência suficiente para afirmar como o servidor da referência
resolve número e contatos preenchidos simultaneamente, duplica destinatários,
nomeia templates sem nome ou trata concorrência e resultados incertos.

Esses limites não serão apresentados como comportamento comprovado. As
decisões de implementação do Orion estão no contrato separado, e os testes
usam contas, contatos, arquivos e transporte sintéticos.
