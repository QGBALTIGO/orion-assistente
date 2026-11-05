# Etapa 08 — observação de Mensagens Programadas

Observada em 2026-11-05 no Chrome conectado do usuário, depois de encerrar
Campanhas e aprovar o WebKit da base publicada. Fonte:
[Mensagens Programadas do BotBot](https://botbot.chat/user/schedule-message?channel=whatsapp).
O título interno é “Mensagens Agendadas”. O método continua sendo o registro
de estados, ações, evidência e limites descrito no README; estes nomes de
observação são nossos, não identificadores produzidos pelo executável REA.

Não foi criado, alterado ou enviado nenhum agendamento na referência. Os
seletores foram exercitados em um formulário não salvo, depois abandonado.
Não houve compra, ativação paga, leitura de código privado ou tentativa de
contornar restrições. Nomes, telefones e identificadores da conta não são
copiados para este registro ou para os testes.

## Lista, abas e filtros

| Observação | Comportamento diretamente observado |
| --- | --- |
| Entrada | “Criar Agendamento” abre o formulário; “Enviar Pendentes Agora” aparece como ação separada. |
| Contadores | “Necessita de atenção” explica “Devido agora ou problema no dispositivo”; “Na fila / Enviando” explica “Já reivindicado pelo agendador”; “Próximo” significa agendado para mais tarde; “Pendência passada” significa agendado antes de hoje. |
| Abas | Ativo, Pendência passada, Histórico e Todos, cada uma com contador. Todas foram abertas. |
| Filtros ativos | Todos ativos, Necessita de atenção, Enfileirado, Enviando e Próximo. Os filtros correspondem a `attention`, `queued`, `processing` e `upcoming`; todos foram abertos. |
| Outras abas | Histórico, Pendência passada e Todos conservam pesquisa/limpeza e não mostram os filtros específicos de ativos. |
| Pesquisa | Campo Pesquisar, botão Pesquisar e Limpar Filtro. A busca mantém a aba/estado e é carregada nos links das outras abas; Limpar Filtro volta a Ativo sem pesquisa. |
| Vazio | A conta não tinha registros de agendamento em nenhuma aba. A interface mostra “Ops, nenhum registro encontrado”. |

Não foi acionado “Enviar Pendentes Agora”: é uma ação de envio global e não
há evidência de que o clique seja apenas uma prévia. A ausência de registros
na lista não é autorização para disparar mensagens a pessoas. A confirmação,
o conjunto de pendências atingido e a política interna dessa ação permanecem
não observados.

## Formulário

| Campo | Estado/limite observado |
| --- | --- |
| Nome do Agendamento | Texto obrigatório; nenhum limite HTML explícito. |
| Selecionar Dispositivo | Uma seleção, com nome e telefone do dispositivo. |
| Grupos | Seleção múltipla dos grupos cadastrados; cada opção mostra sua quantidade de contatos; ação Selecionar Todos os Grupos. |
| Contatos | Seleção múltipla com pesquisa remota e ação Selecione todos os contatos. A pesquisa exibiu Searching… seguido de resultado ou No results found. |
| Data do Agendamento | Obrigatória, mínimo no dia atual da referência, padrão no dia seguinte. |
| Horário do Agendamento | Obrigatório, 1.440 opções de 00:00 a 23:59, com resolução de um minuto; padrão 14:00. |
| Fuso horário | Informação exibida junto do horário, sem seletor. Nesta conta: America/Sao_Paulo. |
| Intervalo Entre Mensagens | 10, 20, 30, 40, 50 ou 60 segundos; padrão 60. |
| Tempo Estimado de Conclusão | Somente leitura; vazio “--” quando não há destinatários. |
| Repetir Mensagem a Cada 30 Dias | Não/Sim; padrão Não. Alterar para Sim não acrescentou outros campos. |
| Tipo de mensagem | Mensagem de texto ou Template de Mensagem. |
| Mensagem | Texto com `maxlength=1000`; ajuda informa substituição de `{name}` pelo nome do contato. |
| Selecione o template | Aparece quando o tipo muda para Template de Mensagem e substitui visualmente o campo de texto. A biblioteca da referência estava vazia. |
| Ações | Criar Agendamento e Voltar. Voltar abandonou o formulário e retornou à lista vazia. |

Os checkboxes personalizados usam o rótulo visível como área de clique: os
inputs têm caixa geométrica vazia. Após inspecionar esse estado, os controles
foram acionados pelos respectivos rótulos, sem forçar ou alterar o DOM.

## Seleção e estimativa exercitadas

Foram exercitados selecionar todos os grupos, todos os contatos, ambas as
opções, um grupo e um contato pesquisado. A seleção não criou agendamentos.

- Sem seleção: a estimativa fica em “--” e a ajuda informa que não há contato.
- Um grupo selecionado atualiza a quantidade da audiência.
- Um contato direto pode ser combinado com um grupo. A ajuda discrimina
  quantidades diretas e provenientes de grupos.
- Quando todos os contatos incluem os membros dos grupos selecionados,
  o total estimado não soma a sobreposição duas vezes. Isso demonstra a
  deduplicação da contagem exibida; não demonstra a implementação interna
  da deduplicação de envio por telefone.
- Trocar 60 por 10 segundos recalculou a data/hora final. A fórmula observada
  é `início + quantidade distinta × intervalo`. Com uma pessoa e intervalo
  de 10 segundos, a estimativa ficou 10 segundos após o início; com duas,
  20 segundos após o início.
- Estimativas longas atravessam a meia-noite e exibem a nova data.

A estimativa observada não comprova a duração real do transporte, o momento
da primeira admissão nem se o intervalo é contado antes ou depois de cada
sequência de template. Esses contratos devem ser declarados pelo Orion.

## Limites da evidência e decisões a explicitar

Não havia registros para observar colunas de linhas preenchidas, detalhe,
edição, cancelamento, progresso, falhas ou histórico real. Também não foram
executados criação, disparo, repetição de 30 dias, entrega, aceitação/leitura,
reconexão, concorrência, recuperação de falhas nem envio de template.

Continuam desconhecidos: o destino de contatos inseridos depois da criação,
a regra de fusos e horário de verão na recorrência, como a referência lida
com ciclos perdidos, se processa pendências de dias anteriores sozinha e o
escopo exato de Enviar Pendentes Agora. O contrato Orion deve distinguir
explicitamente suas decisões para esses casos dos comportamentos observados.

O usuário recusou pagamentos. Não comprar ou ativar complementos e não
solicitar novamente autorização para essa compra. A observação disponível
orienta uma implementação independente no padrão Orion; não permite alegar
equivalência integral com regras privadas não exercitadas.
