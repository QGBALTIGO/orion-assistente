# Loja Delivery

A loja usa a rota `/loja/<slug>` e o estoque compartilhado no painel de Delivery e Distribuição. O proprietário configura endereço de retirada, bairros de entrega, limite de pedidos abertos, horários e formas de pagamento no recebimento. A lista de bairros vem da configuração de Logística. O catálogo expõe somente as variações aprovadas e os nomes e descrições informados para a loja.

## Liberação por empresa

- A empresa precisa concluir a configuração operacional de Delivery. O ambiente precisa incluir seu UUID em `ORION_DELIVERY_PREVIEW_TENANTS` e `ORION_DELIVERY_PUBLIC_TENANTS`. O segundo controle autoriza que o proprietário publique a vitrine.
- O checkout usa `PUBLIC_SITE_SIGNING_KEY` com chave de pelo menos 32 caracteres e separação criptográfica dos tokens de agendamento. O limitador usa o Redis de produção já configurado.
- A publicação valida preço fixo e revisões atuais dos itens. Alterações no produto ou na variação exigem nova aprovação na vitrine. As classes de produtos habilitadas para publicação são água, comida pronta, mercado e utilidades. O segmento Gás e Água aceita apenas água na loja. GLP e outros produtos restritos ficam fora desse checkout.
- O proprietário pode despublicar a vitrine pelo painel. Os pedidos com estoque reservado continuam na fila operacional para conclusão ou cancelamento.

## Fluxo e verificação

O cliente seleciona itens, uma opção de retirada ou entrega e a preferência de pagamento. O servidor verifica preço, quantidade, estoque, horários, área e taxa. A confirmação revalida a cotação assinada e registra uma reserva de estoque em uma única transação. A mesma solicitação retorna a mesma confirmação. O checkout registra nome e telefone como contato do pedido. O vínculo ao cadastro de cliente depende de verificação no atendimento.

O link de acompanhamento contém um código opaco. A consulta mostra número do pedido, andamento, valor total e situação do recebimento. A confirmação do pagamento entra no Financeiro quando registrada pelo operador. Pix, quando configurado na loja, é uma preferência de pagamento no recebimento.

O arquivo `tests/integration/test_delivery_storefront_postgres.py` cobre liberação, produtos visíveis, verificação do resumo, reenvio concorrente, estoque, privacidade, recebimento, cobertura e taxa. Com `ORION_DELIVERY_BROWSER_TEST=1`, executa também o checkout e o acompanhamento no Chromium contra PostgreSQL e Redis isolados, nas larguras utilizadas pelo teste do painel. As imagens ficam no diretório `DELIVERY_EVIDENCE_DIR`.

O checkout público coleta CEP quando preenchido pelo cliente. A seleção de bairro usa a cobertura cadastrada. As janelas de entrega agendada são aceitas pela API e validadas pela política logística. A escolha dessas janelas na vitrine ainda está pendente.
