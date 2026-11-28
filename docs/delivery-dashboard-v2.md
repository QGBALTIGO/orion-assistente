# Dashboard Delivery v2 — 28/11/2026

- Base: API em produção ca9b8be5, commit f7ebcbb. Alterações isoladas em quatro arquivos de aplicação; nenhum módulo WhatsApp ou migração.
- Referências funcionais: [Shopify Analytics](https://help.shopify.com/en/manual/reports-and-analytics/shopify-reports/overview-dashboard/using-the-overview-dashboard) (filtros, comparação e métricas); [Stripe Dashboard](https://support.stripe.com/questions/dashboard-home-page-charts-for-business-insights?locale=pt-BR) (séries e períodos). Identidade visual da Orion preservada.
- Operação diária e desempenho por período claramente separados. Navegação inferior antiga removida; ação Novo pedido no cabeçalho e editor existente.
- Cards com seis indicadores reais; valores de faturamento/recebimentos permanecem separados. Ticket médio do dia baseado nas vendas concluídas desse dia.
- Gráfico SVG real com seleção de indicador e ponto, barras por hora, status por coorte de pedidos, ranking de itens por quantidade, comparação anterior. Zero sem curva falsa ou crescimento inventado.
- Tema escuro neutro restrito à tela. Layout com 2, 3 ou 6 cards conforme largura; análise e listas adaptadas à área disponível.
- A série de vendas brutas do endpoint existente representa eventos de conclusão; ranking usa os mesmos eventos e linhas imutáveis, sem frete. Status e pico usam data de criação dos pedidos. Indicadores não são previsões.
- Publicação condicionada à CI e ao digest do artefato; revisão multimodal em outras branches não incluída.
