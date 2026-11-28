# Delivery — workspace v2

Base publicada: Dashboard v2 (662b396be508df1bc7235e45dbe788e5132b0406). O objetivo é preservar a operação atual e aplicar a mesma linguagem visual aos fluxos operacionais, sem duplicar catálogos, pedidos ou sessões de WhatsApp.

## Etapa 1 — Fundação visual e integração de entrada
- Tema, cabeçalhos com ícones SVG, espaçamento, filtros, estados vazios, cards, botões e diálogos compartilhados, restritos ao workspace Delivery.
- Pedidos, Entregas e Vendas a prazo: cartões reorganizados, origem do pedido visível, ação de detalhe e ações operacionais preservadas; ações secundárias em disclosure nativo.
- Pedidos/Entregas: filtro de origem aplicado no banco; resumo do conjunto completo filtrado (não apenas página visível). Contato manual por WhatsApp nos detalhes quando há telefone confirmado do pedido.
- Produtos: estoque, reservas e saldo calculado em cards distintos; status de catálogo WhatsApp consultado no servidor, usando o mesmo critério de elegibilidade do atendente publicado. A indicação não significa que a sessão está conectada ou que o atendimento está aberto.
- Loja rápida e Conectar WhatsApp: catálogo único, orientação sobre seleção, aprovação, estoque e Pix manual. Não publicar a loja automaticamente nem criar outra sessão.
- Demais telas do nicho: recebem a fundação compartilhada; ainda precisam de revisão funcional e visual individual.

## Próximas entregas necessárias
1. Clientes/CRM, vasilhames, logística e entregadores: revisar os fluxos, a edição, os endereços e os estados reais de operação.
2. Financeiro, vendas a prazo, relatórios, loja e configurações: validar indicadores, permissões, exportações, conciliação manual, navegação e integração individual.
3. Atendimento: integrar as revisões isoladas de multimodalidade, contexto, memória e botões à base publicada atual com testes de regressão.
4. Saída de mensagens: notificações de pedido, despacho, atraso, entrega, cancelamento, pós-venda e avaliação; implementar autorização, deduplicação, estado de envio e opt-out usando o conector existente.
5. Validar o ciclo de ponta a ponta com a conversa de teste autorizada Astra, sem utilizar clientes reais para homologação.

A aprovação dos testes internos não substitui a validação do WhatsApp real. Não declarar 100% concluído até que cada fluxo tenha evidência. Preservar o Dashboard publicado, o banco e os demais nichos.

## Revisão v3
A revisão v2 passou em QA, mas a publicação foi bloqueada por divergência da imagem em produção. A v3 concilia os arquivos com a imagem imutável antes dos testes; conflitos impedem a publicação. O pacote usa exclusivamente os arquivos homologados, conferidos por SHA-256. A interface v3 revisa individualmente as telas operacionais, com filtros expansíveis, ações prioritárias, lista de pedidos, catálogo, vasilhames, logística, relatórios, CRM e financeiro. A integração WhatsApp ainda exige validação real e publicação das revisões multimodais separadas; esta revisão não altera o motor do WhatsApp.

### Comparação dos demais nichos
A base publicada contém alterações de Aparência e Afiliados não presentes na versão histórica do Git. Os testes de outros nichos passam a executar contra a imagem publicada original e contra a revisão reconciliada, com comparação de falhas por teste e erro. Nenhuma falha nova ou alterada é aceita. Incompatibilidades preexistentes continuam visíveis no artefato; não são classificadas como testes aprovados.
