# Etapa 09 — observação de Mensagem em Massa

Observação em 2026-11-05 no Chrome conectado, depois do encerramento da
etapa 08 (PRs 260 e 261, publicação e interface conferidas). Fontes:
[histórico](https://botbot.chat/user/bulk-message?channel=whatsapp) e
[bancada](https://botbot.chat/user/bulk-message/create?channel=whatsapp).
Continua o método de estados, ações e evidência registrado no README após
a análise de `morluto/rea`; os registros abaixo não são saída do executável REA.

Nenhum envio foi iniciado. A preparação foi exercitada com os valores
sintéticos não roteáveis `0`, `0` e `abc`, e texto de teste sem dados pessoais.
Uma linha foi removida, e a outra desapareceu ao recarregar. Não foram
alterados contatos, grupos, dispositivos ou templates. Não houve compra.
Nomes e números reais exibidos pela conta não são copiados para os testes.

## Histórico

- Entrada intitulada “Histórico de transações”, com ação “Mensagem em Massa”.
- Três indicadores: Mensagens Enviadas, Mensagens dos últimos 30 dias e
  Transação de hoje. A conta tinha oito registros antigos e zero nos dois
  períodos recentes; não se infere a fórmula interna dos períodos.
- Colunas: Mensagem de, Mensagem Para, Tipo de Mensagem, Nome do template,
  Enviado Em. As linhas observadas eram de texto simples, com data/hora.
- Não foram observados reenvio, exclusão de histórico, filtros ou paginação
  nessa lista pequena. Não se infere ausência desses recursos em outros estados.

## Bancada antes do envio

| Controle | Observação |
| --- | --- |
| Escolha os Contatos para Enviar | Abre um diálogo de preparação. |
| Progresso | Inicialmente `0/0`; duas linhas adicionadas produziram `0/2`. |
| Atraso | Campo numérico, mínimo 10, máximo 60, padrão 10 segundos; sem atributo `step` explícito. |
| Iniciar | Ação distinta da adição de contatos; não acionada. |
| Tabela | Dispositivo, Destinatário, Mensagem, Status e Enviado Em. |
| Linha de texto | Campo editável individualmente; alteração refletida na linha. |
| Remoção | Link junto ao destinatário removeu somente a linha selecionada; progresso passou de `0/2` para `0/1`. |
| Estado inicial de linha | “Esperando”, sem data de envio. |
| Recarregar | Descartou a linha ainda não iniciada e retornou a `0/0`. |
| Campanhas | Link de sugestão para os recursos avançados; não foi usado para contratar complemento. |

## Diálogo de destinatários e conteúdo

Um dispositivo é escolhido por adição. A escolha é feita antes de acrescentar
as linhas à bancada. O diálogo oferece três abas:

- **Selecionar Grupos:** seleção múltipla dos grupos existentes. A ajuda
  informa máximo de 500 contatos dos grupos escolhidos, com duplicatas
  removidas. Essa regra foi lida na interface; não foi feito disparo para
  verificar sua implementação no transporte.
- **Selecionar Contatos:** pesquisa/seleção múltipla e controle para todos
  os contatos, com máximo de 500 informado na tela.
- **Entrada Manual:** uma linha por telefone, incluindo código do país.
  A ajuda informa limpeza para conservar somente dígitos. Não há importação
  de arquivo ou CSV nos controles observados.

O conteúdo é texto com `maxlength=1000`, ou um template existente. Ao
selecionar Template, o campo de texto é substituído pelo seletor da biblioteca,
que estava vazio. A ajuda de texto apresenta negrito, itálico, tachado e
monoespaçado do WhatsApp. Não foi observada ajuda de `{name}` nesta tela.
Os controles personalizados de tipo têm input sem área geométrica; os rótulos
visíveis correspondentes foram usados para a alternância, sem forçar o DOM.

“Adicionar Contatos” fecha o diálogo e prepara linhas; “Cancelar” abandona
o diálogo. A tentativa vazia não acrescentou linhas; não foi capturada uma
mensagem de validação que permita afirmar seu texto ou sua ordem.

### Validação manual efetivamente observada

Com `0\n0\nabc` e texto de teste, a referência preparou **duas linhas `0`**.
O trecho sem dígitos não virou linha. Portanto, nessa preparação, duplicatas
manuais foram preservadas e o formato de telefone não foi validado. Isso não
comprova que o transporte aceite ou envie esses números inválidos. As linhas
ficaram em “Esperando”; não foi clicado Iniciar.

## Limites da observação

Não foram exercitados envio, pausa durante envio, falhas reais, retomada,
recarga durante execução, resultado incerto, capacidade, confirmação de
recebimento/leitura, múltiplas contas de dispositivo, templates com conteúdo,
nem o teto de 500 com uma audiência real. A seleção de dispositivo por adição
e a coluna por linha sugerem suporte a linhas com dispositivos distintos,
mas esse caso não foi provado com uma segunda sessão disponível.

Esses limites não impedem implementar um contrato completo no Orion; exigem
separar observação, inferência e decisões próprias. Não se afirma equivalência
empírica integral de comportamentos privados não testados.
