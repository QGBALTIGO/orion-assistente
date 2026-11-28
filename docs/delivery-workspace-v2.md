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
