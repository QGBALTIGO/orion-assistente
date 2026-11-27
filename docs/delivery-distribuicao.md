# Delivery e Distribuição

## Referência e escopo observado

Em 27/11/2026, a conta autenticada do FazPedidoPro permitiu observar o Dashboard, a lista de pedidos, a venda rápida até a seleção de produtos, o catálogo e o histórico de estoque, além das configurações de loja, equipe, vasilhames, venda a prazo e relatórios. O estoque zerado impediu avançar na venda rápida. Nenhum pedido, ajuste ou configuração foi gravado na conta. Despacho, cliente ausente, entrega concluída, quitação parcial e compra completa na loja foram descritos pela referência oficial e não foram executados nessa sessão. [Interface do FazPedidoPro](https://fazpedido.pro/app) · [FAQ oficial](https://fazpedido.pro/faq/)

O pacote REA 6.4.0 foi localizado no cache do npm. A instalação isolada ficou bloqueada por dependências ausentes no cache e pela indisponibilidade do registro. A evidência de interface veio da sessão autorizada no navegador. [REA](https://github.com/morluto/rea)

| Área | Evidência da referência | Implementação na Orion |
| --- | --- | --- |
| Dashboard e pedidos | Listas, data, indicadores e busca observados. A FAQ descreve criação, cancelamento, rastro de atividades e mensagens iniciadas pelo operador. | Dashboard por dia e fuso do negócio, recebimentos registrados, fila de entrega e estoque baixo. Pedidos com busca, filtros e histórico. Venda rápida de balcão, retirada e entrega. |
| Catálogo e estoque | Busca, status, categoria, cadastro e ajustes observados. A FAQ descreve indisponibilidade sem saldo e visibilidade separada na loja. | Produto, variação, preço, custo, categoria, código de barras e estoque mínimo. Entrada, saída, contagem, reserva, liberação, baixa e devolução com histórico. Após 24 horas da observação, o saldo exige nova contagem para confirmar vendas. |
| Clientes | Cadastro e busca observados. A FAQ descreve histórico de compras, endereço e vasilhames. | Cadastro rápido e prevenção de duplicidade por telefone na mesma empresa. Pedido vinculado ao CRM, cópia do último endereço de uma entrega concluída sob confirmação e relatório de vendas por cliente. |
| Cobertura e entregas | Fila vazia e configurações de cobertura observadas. A FAQ descreve cliente ausente, reagendamento, recebimento e entrega. | Taxa por cidade, UF e bairro, exclusões, janelas e antecedência de agendamento. Cadastro e seleção manual de entregador. O pedido em rota pode voltar à fila após confirmar o retorno dos itens. Ausência e reagendamento ficam no histórico. |
| Vasilhames | Cadastro e histórico disponíveis, sem empréstimos registrados na conta observada. A FAQ descreve troca, vazios recebidos e saldo por cliente. | Estoque separado de vazios, saldo em comodato por cliente e histórico. O operador confirma troca e empréstimo ligados aos itens do pedido na conclusão. Uma devolução recompõe estoque e desfaz as movimentações que ainda podem ser revertidas, com validação de saldo. |
| Recebimentos e venda a prazo | Filtros de cobrança observados. A FAQ confirma baixas parciais e saldo remanescente. | Recebimentos em dinheiro, cartão ou Pix informados e confirmados pelo operador. Venda a prazo exige cliente e vencimento e permite baixas parciais ou integral. Cada baixa registra um lançamento financeiro. Devolução integral encerra o saldo a receber e registra estorno do que foi efetivamente recebido, quando confirmado pelo operador. |
| Financeiro e relatórios | Despesas, pagamentos e abas de relatórios observados. A FAQ descreve rastreabilidade. | Despesas e lançamentos usam o Financeiro existente, conforme a permissão da empresa. Relatórios de vendas, produtos, clientes, posição e movimentações de estoque, contas a receber e vasilhames. Cada CSV usa os mesmos filtros da tela e exporta o conjunto completo do recorte. |
| Loja rápida | Opções de catálogo, horário, retirada e cobertura observadas. A FAQ descreve pedido sem login, escolha de produtos, endereço e pagamento na entrega. | Vitrine opt-in por empresa, com escolha de produtos, horários e formas de recebimento. Carrinho, retirada ou entrega elegível, confirmação de preço e reserva de estoque no servidor. Acompanhamento pelo código privado retornado ao cliente. Pagamento escolhido na loja indica preferência e depende de confirmação posterior. |
| Funcionários | Lista de equipe e convite observados. A FAQ documenta login de equipe e permissões. | Reutilização das contas e permissões do painel. Cadastro de entregadores para atribuição manual. Operação externa por aplicativo de entregador ainda não está incluída. |

## Portas de publicação e segurança

- A área do painel requer uma conta pessoal vinculada à empresa de Delivery. A lista de empresas de avaliação é definida em `ORION_DELIVERY_PREVIEW_TENANTS`. A ausência da empresa na lista desabilita os módulos para ela.
- A vitrine começa desativada. Exige o mesmo tenant em `ORION_DELIVERY_PUBLIC_TENANTS`, publicação explícita no painel e catálogo revisado. Só são publicados itens ativos com preço fixo autorizados. O modo entrega exige política de cobertura. A loja aplica validação de acesso, limites de uso e orçamento assinado antes de reservar o estoque. O código de acompanhamento é entregue ao comprador e a resposta de acompanhamento não inclui dados pessoais.
- A loja restringe categorias e exclui os itens regulados identificados na revisão do catálogo. No tipo Gás e Água, a categoria pública admitida é água. A ativação de GLP requer análise separada das condições de revenda e distribuição. [Revenda de GLP](https://www.gov.br/anp/pt-br/assuntos/distribuicao-e-revenda/revendedor/glp) · [Distribuição de GLP](https://www.gov.br/anp/pt-br/assuntos/distribuicao-e-revenda/distribuidor/glp)
- O checkout público depende da chave de assinatura do site e do limitador de acesso. Mudanças de preço ou da revisão do catálogo exigem nova conferência. A exposição de cada loja é independente da publicação da imagem no Railway.
- Pendências de produto incluem consulta de CEP e múltiplos endereços por cliente, integração Pix com provedor e pagamento antecipado, WhatsApp transacional, alertas de cobrança, parcelamento programado, aplicativo do entregador, roteirização automática, rastreamento por localização, entrega recorrente e fidelização. Pagamento parcial registrado e escolha de Pix como preferência já existem.

## Interface

O painel de Barbearia define os padrões de navegação, formulários, botões e diálogos compartilhados em `app/static/orion-suite.js`, estilos em `app/static/orion-suite.css` e máscaras em `app/static/orion-fields.js`. Delivery reaproveita os componentes e os temas. A loja tem recursos próprios e mantém a identidade visual da Orion.

## Banco de dados e homologação

| Migrações | Alterações |
| --- | --- |
| `0099` a `0101` | Detalhes de catálogo com RLS, pedidos e reservas, fila de despacho, taxa e endereço associados ao pedido. |
| `0102` a `0104` | Estoque de vazios, empréstimos, venda a prazo e vínculo da devolução com o estorno financeiro. |
| `0105` a `0108` | Política de cobertura, janelas e entregadores, vitrine opt-in e pedidos públicos, movimentações de vasilhames ligadas ao pedido e baixa parcial de contas a receber. |

O workflow `delivery-review.yml` roda o painel com PostgreSQL e Redis isolados, autenticação e dados sintéticos. A revisão anterior gerou capturas em nove larguras e nos modos claro, escuro e sistema. A execução correspondente ao código publicado deve ser conferida antes de aprovar a implantação. [Homologação do painel](https://github.com/QGBALTIGO/orion-assistente/actions/workflows/delivery-review.yml)

O projeto separado de homologação foi criado no Railway, onde o plano gratuito bloqueou a criação do primeiro serviço. O workflow `delivery-release.yml` prepara uma imagem com as mudanças de Delivery sobre a base de produção fixada, ensaia a cadeia de migrações e a compatibilidade de retorno à imagem anterior com o banco avançado, e publica a imagem com digest no GHCR. O workflow não executa o deploy do Railway. A validação da implantação requer conferir os logs de backup e migração, a saúde do serviço e a visibilidade das rotas apenas para as empresas autorizadas. [Workflow de publicação](https://github.com/QGBALTIGO/orion-assistente/actions/workflows/delivery-release.yml)
