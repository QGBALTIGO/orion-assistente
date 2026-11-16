# Etapa 03 — Testar Chatbot

Fonte: `https://botbot.chat/user/chatbot/test-interface?channel=whatsapp`.
Observação pelo Chrome autorizado em 2026-11-04. Método REA já registrado no
README: observar o estado, exercer o fluxo, registrar o resultado e distinguir
comportamento demonstrado de decisões próprias do Orion.

Não foram alteradas regras, dispositivos, templates ou configurações da
referência. Os testes abaixo ocorreram na interface de teste, com texto
sintético ou uma regra de texto previamente inspecionada. Não foi acionado o
botão separado de envio real para telefone.

## Estados e transições observados

| Estado/ação | Resultado observado |
|---|---|
| Abrir sem dispositivo | Seletor obrigatório; saudação inicial; zero mensagens; campo e envio desabilitados. |
| Selecionar dispositivo | Cabeçalho mostra o dispositivo e seu número; campo de mensagem habilitado; seleção é refletida em `device_id` na URL. |
| Enviar texto pelo Enter | Balão do usuário aparece com o texto aparado, seguido da resposta de teste; ambos têm horário e entram no contador. |
| Variação de caixa e espaços | Uma palavra-chave de texto com caixa diferente e espaços nas extremidades encontrou a mesma resposta. |
| Texto sem correspondência | Aparece um balão de resposta com “Nenhuma resposta correspondente ao seu texto para este dispositivo”; o contador inclui esse balão. |
| Palavras-chave disponíveis | Após o teste, a interface apresenta atalhos de palavras-chave com indicação de correspondência, como `exact` e `contains`. Clicar em um deles dispara a mensagem de teste. |
| Entrada | Campo HTML `INPUT`, de uma linha, com `maxlength=400`, contador visível e instrução para usar Enter. |
| Entrada artificial de 401 caracteres | O preenchimento automatizado ultrapassou o atributo HTML; a interface mostrou 401/400 e adicionou a entrada ao histórico, mas não apareceu resposta nem erro visível. Essa falha não define o limite pretendido: o Orion deve validar os 400 no cliente e servidor. |
| Limpar Chat | Remove o histórico imediatamente, sem confirmação; zera o contador e mantém o dispositivo selecionado. |
| Remover/reselecionar dispositivo | Preserva os balões e o contador; sem seleção, desabilita o compositor e troca o cabeçalho. Reselecionar habilita novamente. |
| Recarregar com conversa existente | A seleção permanece pela URL, e o histórico volta a zero. A conversa não é restaurada. |
| Tela cheia | Expande a área via CSS, sem usar o fullscreen nativo do navegador. O botão passa a “Sair da tela cheia”. Escape restaura a tela e preserva os balões. |
| Abrir a partir de uma resposta | O card do Chatbot contém link com `device_id` e `reply_id`; ele seleciona o aparelho e faz um teste inicial automaticamente. Uma mensagem posterior volta à correspondência normal. |

O título da referência identifica a função como beta. A ajuda anuncia testes
de respostas ativas e inativas, correspondência exata/contém/lista e os cinco
coringas com suas frequências. Não há seletor de status na conversa.

O link **Testar Chatbot** e o botão **Testar Resposta** são distintos no card:
o primeiro abre esta conversa de teste; o segundo recebe um telefone e envia
uma resposta real. A etapa 03 acrescenta o primeiro sem substituir o segundo.

## Limites da observação

- A conta oferecia apenas um dispositivo. Não foram alterados seu estado,
  conexão, horário ou proprietário para provocar outras condições.
- As duas regras inativas disponíveis continham uma ação POST para um
  serviço externo. Seus formulários foram inspecionados, mas não executamos
  essas ações nem copiamos URLs, tokens ou conteúdo privado para o Orion.
  A inclusão de inativas é anunciada pela ajuda; sua resposta externa não
  foi validada ao vivo.
- Não havia coringas visíveis para exercitar as frequências dentro da
  conversa de teste. Tampouco foi observada uma seleção nativa de lista,
  carrossel ou prévia de arquivo nessa conversa.
- A lista de palavras-chave agrupa entradas; a conta possuía palavras-chave
  repetidas com tipos diferentes. A precedência interna desse agrupamento
  não foi inferida como um contrato de IDs do Orion.
- As respostas de texto da referência preservam os marcadores literais de
  formatação no balão. Os testes não comprovam entrega em um cliente WhatsApp.

## Contrato de adaptação para o Orion

A conversa deve usar componentes, fontes, cores e espaçamentos do Orion.
Ela fica imediatamente após Chatbot na navegação, somente no perfil
`subscription_services`, usando a permissão `chatbot.manage`. O envio real
existente permanece separado.

O backend seleciona e expande o mesmo grafo de regras em cópias de memória.
A inclusão de inativas é uma política exclusiva dessa simulação: não altera
flags, revisões, regras ou o motor de produção. Cada pedido valida novamente
proprietário, empresa, dispositivo e revisão atual.

Não são criados eventos, jobs, runs, efeitos, cooldowns ou seleções de menus
reais. O teste não chama WhatsApp, ASR ou URLs configuradas. Ele verifica
correspondência e conteúdo; horários e frequências continuam pertencendo à
execução real. A interface deve deixar essa distinção clara em texto breve.

Texto, legendas, templates e referências usam o plano declarativo já publicado.
Arquivos normalizados da própria empresa podem ser lidos por uma rota privada
com rechecagem de autorização. Listas podem ser exercitadas dentro do contexto
da simulação, sem usar tokens de mensagens reais. Carrosséis exibem seu texto e
botões; imagens externas e ações HTTP são identificadas como etapas não
executadas, sem inventar imagem ou retorno do servidor. Esses detalhes ricos
são decisões do Orion sobre seu próprio plano, não alegações de observação na
referência.

O histórico permanece somente na tela. Limpar, trocar dispositivo ou sair
invalida solicitações pendentes e interações anteriores; a troca de dispositivo
preserva os balões visíveis. Trocar empresa ou desmontar a tela remove o estado.
Uma resposta tardia não pode aparecer na conversa de outro contexto.

## Qualificação necessária

Validar entrada vazia, 400/401 unidades UTF-16 e emojis; palavras-chave e
inativas; ausência de correspondência; ordem de sequências e referências;
seleção de lista; URLs declarativas sem rede; leitura privada de mídia; revisão
obsoleta, revogação e escopo de empresa; ausência de escrita/efeitos; requisições
concorrentes e limpeza durante a espera; deep link executado uma vez por
abertura; teclado, tela cheia, celular/tablet/desktop e dois temas.

Os testes usam regras, contatos e arquivos sintéticos, com PostgreSQL real no
CI descartável e provedores externos bloqueados. A publicação desta etapa
precisa ser verificada antes da investigação de Templates, etapa 04.
