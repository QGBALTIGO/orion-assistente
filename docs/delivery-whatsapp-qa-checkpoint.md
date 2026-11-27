# Delivery — checkpoint de execução (27/11/2026)

Missão ativa: atendimento WhatsApp, CRM persistente, laboratório de homologação, multimodalidade, pedidos, logística, notificações, segurança e validação de ponta a ponta. Novas solicitações complementam a missão; não substituem as etapas pendentes.

## Evidência já obtida
- Produção estável anterior: commit 4879bf3, imagem API `6bec942197e7...`, deploy a81801c7. Sessão Astra preservada.
- Conversa real: consulta de água, três perguntas simultâneas, coleta de nome, forma de pagamento, troco, confirmação textual.
- Pedido sintético 52383F73, Cliente QA Orion, retirada, total R$ 10,00, troco R$ 40,00. Origem WhatsApp conferida na tela de detalhes; nenhuma entrega ou pagamento real.
- Loja de avaliação renomeada para Distribuidora Orion - Ambiente de Testes; despublicada. Não equivale a um ambiente de carga isolado da produção.
- Botões: revisão be952d3, testes locais 8 Python + 4 Node aprovados; build de ambas as imagens aprovado. Homologação 46079675515 reprovada na segunda compra (nome solicitado novamente). Não implantar esse build.
- Causa: checkout armazenava o nome/telefone na cotação, mas só vinculava o CRM na conclusão operacional. A nova revisão cria/reutiliza a identidade no momento da confirmação, com isolamento por empresa e telefone; adiciona evidência do perfil CRM e teste de segurança.
- A tela do WhatsApp no modo atual pode ser lida, mas não controlada pelo conector Browser. Testes interativos adicionais requerem modo Work conectado ou outro ambiente autorizado com capacidade de interação.

## Próximas verificações obrigatórias
1. Homologar a correção do CRM, dos botões, do troco combinado e da prioridade de emergência; só publicar imagens aprovadas.
2. Testar botões nativos na conversa autorizada com Astra, com alternativa textual, sem ampliar escopos de outros nichos. Repetir confirmação, clique duplicado, botão antigo, cancelamento e nova compra.
3. Confirmar cadastro no CRM e vínculo histórico; implementar e verificar múltiplos endereços, recuperação autorizada, alterações e preferências sem sobrescrita silenciosa.
4. Completar o laboratório de testes com catálogo/estoques sintéticos, entregadores, áreas, taxas e clientes. Não realizar pagamentos, entregas, mensagens a terceiros nem testes de carga no banco compartilhado.
5. Áudio: o broker de Delivery ainda aceita somente texto. Reaproveitar ASR existente com autorização de modalidade, isolamento, quotas e recuperação; testar mídia real. Imagens/localização/chamadas dependem de eventos e integrações apropriados; não declarar recebimento sem prova.
6. Catálogo: atualmente apenas itens de água explicitamente aprovados podem ser processados automaticamente; gás e demais itens restritos são encaminhados à equipe. Preservar controles de segurança, revisão de elegibilidade e encaminhamento em emergências.
7. Completar logística, pedidos simultâneos, estados operacionais, notificações, pós-venda, Pix manual e monitoramento. Testar falhas e garantir isolamento dos demais nichos.
8. Matriz de 500 cenários distintos: registrar cenários reais, simulados, reprovados e bloqueados separadamente, com evidências. Não contar cenários planejados como executados.

Não declarar missão integralmente concluída sem evidência. Cada ciclo deve preservar os resultados e os bloqueios, e os commits/builds não substituem validação real.

## Checkpoint atualizado — CRM, memória e áudio
- Revisão `f7ebcbb`: integração CRM corrigida e emergência priorizada; CI 46081166451 aprovado, com 90 + 4 + 79 testes Python e 4 testes Node explicitamente contados nos logs (177 execuções, não 177 cenários distintos da matriz). Imagens API/QR no workflow 46081189864 aprovadas.
- Produção: deploy API `91c79671-1b46-4dd1-a160-891f42374dd3`, digest `ca9b8be591f7d180f69b6cc158991266eded13cf8a3aaeaed400b305b121dee9`, SUCCESS. QR bridge e worker preservados. Reteste conversacional real da nova API pendente.
- Botões nativos: código e transporte aprovados em homologação; conector de produção não atualizado, pois o escopo exato do canal de teste não pôde ser confirmado. Alternativa textual ativa.
- Memória de endereços: branch `codex/delivery-memory-20261127`, revisão `9c29a29` aprovada em CI 46081391406; revisão `282cbfb` acrescenta teste de gravação após checkout confirmado, CI 46081927436. Não implantada.
- Piloto áudio: branch `codex/delivery-audio-20261127`, revisão `fa1361a`, CI 46081789930 aprovado integralmente (review e validation). Reutiliza ASR existente, escopo por tenant, quotas e fallback; confirmação final por texto/botão. Não implantado, sem teste de áudio real.
- Matriz em `docs/delivery-whatsapp-qa-matrix.csv`: 500 cenários planejados e quatro evidências históricas reais. Os testes automatizados são registrados separadamente; não equivalem automaticamente a cenários individuais aprovados.
