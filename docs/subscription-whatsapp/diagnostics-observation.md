# 06 — Diagnósticos: observação e contrato

## Escopo e ponto de partida

Observação iniciada em 2026-11-04, depois da publicação e conferência ao vivo
de Mensagens Enviadas (PR 252, produção
`f58730ac8e380bd2c12286c69c1db3873d3cbf0f`, árvore
`28ac47dc292556b77b80fbc80607077265011a41`). O módulo pertence somente ao
workspace `servico_assinaturas`; interface, conteúdo de teste e implementação
serão próprios do Orion.

Fonte: `https://botbot.chat/user/diagnostics?channel=whatsapp`, observada pela
extensão do Chrome autorizado. Nomes, números, conteúdo das mensagens e
configurações privadas da conta de referência não integram este registro.

## Comportamentos observados

| Estado/ação | Resultado visível | Limite da observação |
|---|---|---|
| Abrir Diagnósticos pelo menu WhatsApp | Título, link Atualizar e seletor de dispositivo inicialmente vazio | O link Atualizar aponta para recarga da página; não foi acionado depois da seleção. |
| Selecionar o dispositivo disponível | Exibe os grupos de status, últimas mensagens, proxy e mensagem de teste | Somente um dispositivo já conectado estava disponível. |
| Status do Dispositivo | Separa Status da Conexão de Status do Servidor; ambos apareciam ONLINE, e o servidor incluía latência em ms | Não foi induzida desconexão nem falha de servidor. |
| Últimas Mensagens | Tabela Hora, Destinatário, Mensagem e Status; três linhas foram exibidas | Não há evidência de paginação, filtro ou de um limite formal além dessas três linhas observadas. O significado exato de cada estado não foi inferido como entrega física. |
| Diagnósticos de Proxy | Host, porta e status; a referência indicava proxy compartilhado; botão Testar Conexão de Proxy | O botão não foi acionado. Não copiar a configuração privada nem declarar um serviço comercial que o Orion não oferece. |
| Mensagem de Teste | Telefone opcional, instrução para incluir código do país e botão Enviar Mensagem de Teste | O placeholder informa que vazio usa o número do próprio dispositivo. O envio manual e a validação de telefone inválido não foram executados. |
| Primeira seleção do dispositivo | A interface iniciou automaticamente uma mensagem de teste para o próprio número, exibiu progresso e depois sucesso | Esse efeito automático só ficou evidente após a seleção. Não houve clique no botão de envio; não foi verificado recebimento físico. |

### Efeito automático da referência

A seleção, que inicialmente se apresentava como consulta de diagnóstico,
acionou um envio automático para o próprio dispositivo. A referência informou
“Teste Automático Bem-Sucedido” e que a mensagem havia sido enviada. O usuário
foi informado do ocorrido, e a navegação saiu dessa página para evitar
repetição. Não se afirma ausência de envio nessa observação específica.

Não foram acionados os botões de envio manual ou teste de proxy, nem alteradas
configurações, reiniciado, removido ou desconectado o dispositivo. Não foram
provocados estados de erro por intervenções na conexão em uso. As
validações do Orion serão feitas com contas, números e provedores sintéticos.

## Decisões independentes do Orion

- Abrir, selecionar dispositivo e atualizar o diagnóstico não admitem mensagens
  nem iniciam conexão, reinício, pareamento, transcrição ou webhook. O teste
  de mensagem será uma ação explícita com confirmação do destinatário.
- A separação de estado da conexão e disponibilidade do servidor será mantida.
  Um servidor acessível não comprova conexão WhatsApp; timeout não será
  convertido em diagnóstico definitivo de desconexão.
- A origem, o instante e o frescor da informação precisam ficar explícitos.
  Dados antigos ou indisponíveis não serão apresentados como uma medição atual.
- A tabela recente usará tentativas reais do dispositivo autorizado, com campos
  públicos selecionados e indicação honesta de aceitação, falha ou incerteza.
  Não expor payloads internos, segredos, recibos privados ou IDs do provedor.
- O diagnóstico de proxy deve representar a configuração efetiva do Orion,
  incluindo conexão direta ou ausência de recurso quando aplicável. Um teste
  só poderá usar a configuração já autorizada do dispositivo; o formulário
  não aceitará URL arbitrária para sondagem nem publicará credenciais.
- A mensagem de teste terá conteúdo próprio e congelado, confirmação, CSRF,
  idempotência, reautorização antes do transporte e recuperação por consulta
  após resposta perdida. “Aceito pelo transporte” não será chamado de entrega.
- Nenhuma ação de reparo automático, exportação, limpeza de histórico ou
  administração global foi observada neste módulo e não será presumida.

Essas decisões preservam as capacidades observadas e registram expressamente
a diferença de consentimento no envio de teste. Não se declara equivalência
integral dos estados da referência que não foram executados.

## Estado do trabalho

Observação concluída. A implementação independente do Orion está em
qualificação, com intenção durável, confirmação explícita e probe de proxy
separado da sessão WhatsApp. O contrato técnico está em
[diagnostics-contract.md](diagnostics-contract.md). Campanhas (etapa 07)
ainda não foi investigado.
