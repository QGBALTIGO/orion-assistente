# Etapa 07 — observação de Campanhas

Observado em 04/11/2026, depois de concluir e conferir Diagnósticos em produção
(PR 253, árvore `46d4109b71afa2c986c6580ac994c0113725a17f`). A implementação é
independente e usa a identidade visual e os componentes do Orion, somente no
nicho `servico_assinaturas`.

## Superfícies observadas

- Lista `https://botbot.chat/user/campaigns?channel=whatsapp`: pesquisa por
  título; criar campanha; colunas título, dispositivos, data de início, status,
  progresso e ações visualizar, editar e excluir. Um registro concluído mostra
  porcentagem e contatos processados/total.
- Formulário de criação: título, um ou mais dispositivos, texto simples ou
  template, data e hora de início, início/fim da janela diária, fuso IANA, dias
  da semana, intervalo e grupos de contatos. A seleção de dispositivos e grupos
  oferece marcar/desmarcar todos. Dias oferecem todos, nenhum, dias úteis e fim
  de semana. Padrões observados: America/Sao_Paulo, 08:00–18:00, segunda a sexta
  e 60 segundos. Intervalo permitido: 30–60 segundos. Texto: até 3.500 caracteres
  no formulário, com marcadores `{name}` e `{phone}`. Template substitui a caixa
  de texto por um seletor. Uma nota informa no máximo 10 contatos por minuto e
  que a fila pode demorar mais que o intervalo configurado.
- Detalhe: informações da campanha; progresso; totais, enviados, pendentes e
  falhados; grupos e quantidade de contatos; três seções pesquisáveis de
  enviados, pendentes e falhados. A tabela de enviados mostra contato, WhatsApp,
  mensagem, dispositivo, instante e estado. Ações editar e excluir visíveis.

## Limites da observação

A conta informa que o complemento Campanhas é necessário para criar. O
formulário é visível, mas não foi submetido. A ação Editar levou a uma resposta
de bloqueio pelo complemento. Não se contornou o bloqueio e não se comprou o
complemento. Nenhuma campanha, grupo, contato ou mensagem foi criado, alterado,
excluído, agendado ou enviado na referência. A aba foi devolvida à lista.

Não foram observados a execução do scheduler, a distribuição entre dispositivos,
as regras para editar campanhas em andamento, o tratamento de contatos repetidos
ou o significado técnico de “Enviado”. Esses comportamentos são decisões
explicitadas no contrato Orion, não alegações sobre código privado da referência.
Não se copiam nomes, números, mensagens, grupos, marca ou código da conta.

## Dependências no Orion

O Marketing IA existente pertence ao painel comum e usa audiências calculadas e
outro transporte. Seus grants não são concedidos ao workspace de assinaturas.
O workspace de assinaturas ainda não tem grupos próprios. O módulo inclui a
gestão mínima de grupos necessária ao seletor, sem liberar o Atendimento ou
Marketing comuns. A capacidade atual de Dispositivos permanece uma conexão ativa
por empresa; o contrato usa uma lista de dispositivos e aceita os disponíveis.

Testes de envio, horários, interrupções, mídias e recuperação serão sintéticos.
A verificação de produção será somente de leitura e não enviará mensagens reais.
