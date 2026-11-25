# Dashboard de Afiliados — novembro de 2026

## Experiência

O Dashboard usa os tokens, a tipografia e o destaque azul do painel de barbearia,
com uma composição própria para Afiliados. A largura disponível dentro do painel
determina a disposição por container queries, inclusive quando a sidebar reduz
o espaço em tablets. Os temas claro e escuro usam as cores existentes da Orion.

O painel oferece período compartilhado (Hoje, 7 dias, 30 dias e Este mês),
consulta manual, operação atual, destinos, próximas publicações, atividade recente
e avisos com ações. Uma fonte financeira válida habilita os indicadores de
comissão, valor vendido e pedidos, o gráfico diário e o ranking de produtos.
Os dias do gráfico podem ser consultados por toque ou teclado. Uma falha de
consulta preserva o último relatório confirmado do mesmo período.

## Disponibilidade dos dados nesta entrega

O serviço de Afiliados hoje fornece `state`, uma janela de até 120 registros,
além de conexões, destinos e regras. Isso sustenta contagens operacionais atuais,
mas não sustenta totais históricos por período. `changed` indica a última
atividade do registro; não há comprovação de que seja a data de publicação.

Por isso, a ausência de um relatório financeiro produz valores `null`, sem
gráficos inventados ou receita calculada a partir do preço de catálogo. O estado
sem integração mostra as métricas operacionais e uma ação para preparar ofertas,
além do acesso ao Portal de Afiliados Shopee. Não apresenta receita zero como se
um relatório tivesse sido consultado com sucesso.

**A sincronização de vendas, comissões e cliques não é ativada por esta entrega.**
O código e o acesso administrativo do serviço de Afiliados na VPS não estavam
disponíveis nesta sessão. As credenciais Shopee já conectadas permanecem nesse
serviço; não foi criada uma segunda cópia dessas credenciais na Orion.

## Consulta e isolamento

`GET /api/v1/afiliados/{tenant_id}/api/analytics?period=month`

O acesso exige a mesma identidade verificada de proprietário, empresa ativa e
nicho Afiliados utilizada pelo restante do módulo. A consulta ao provedor é
assinada para a empresa, o método e o caminho específicos. O parâmetro de período
aceita somente `today`, `7d`, `30d` ou `month`.

O proxy tenta obter `/api/analytics` no serviço de Afiliados. Apenas a ausência
da capacidade (404, 405 ou 501) utiliza o builder operacional. Erros de permissão,
limite de chamadas e falhas do serviço não são transformados em relatórios vazios.
O fallback consulta `/api/state`, resolve o fuso cadastrado da empresa e devolve
somente os campos explicitamente permitidos. As respostas usam `no-store`.

Os períodos são datas locais inclusivas: sete dias incluem hoje e os seis dias
anteriores; trinta dias incluem hoje e os 29 anteriores. `month` começa no dia 1
do mês local. A operação atual é independente desse período e está identificada
como amostra das últimas 120 publicações.

### Contrato do relatório

| Campo | Significado |
| --- | --- |
| `period` | Chave, rótulo, datas inclusivas e fuso IANA |
| `updated_at` | Instante UTC da consulta ou relatório |
| `sources.sales` e `sources.clicks` | Estado, motivo e última sincronização de cada fonte |
| `sources.publications` | Cobertura histórica; fallback usa `complete: false`, `scope: latest_120` |
| `summary.commission_cents` | Comissão bruta estimada, em centavos; ausência é `null` |
| `summary.validated_commission_cents` | Comissão validada segundo uma fonte explícita, sem implicar pagamento |
| `summary.sales_cents` | Valor vendido atribuído, separado da receita do afiliado |
| `summary.orders`, `items`, `clicks` | Contagens confirmadas pela fonte; ausência é `null` |
| `summary.published` | Total confirmado no período; fallback usa `null` |
| `operation` | Contagens da amostra atual, seu tamanho e limite |
| `series` | Um registro por data; campos desconhecidos continuam `null` |
| `products`, `destinations` | Rankings no nível de atribuição efetivamente disponível |
| `upcoming`, `recent` | Campos públicos das publicações, sem recibos, tokens ou erros brutos |

## Próxima integração com a Shopee

O Explorer oficial brasileiro foi consultado em 25/11/2026. A operação
`conversionReport` permite consultar conversões, pedidos, itens, valores e
comissões. A implementação deve usar as credenciais existentes no serviço de
Afiliados e verificar a permissão real para relatórios. Uma conexão de catálogo
validada não comprova, por si só, acesso ao relatório financeiro.

A sincronização precisa deduplicar conversões, pedidos e itens, atualizar
cancelamentos/reembolsos e somar a comissão de cada conversão uma única vez.
Valores monetários devem usar representação decimal no cálculo e centavos no
contrato da interface. A semântica do valor efetivo de item deve ser conciliada
com uma resposta real antes de qualquer multiplicação por quantidade.

Pedidos concluídos não são automaticamente comissões validadas ou recebidas.
`validatedReport` exige o identificador de validação. O Explorer público não
oferece uma operação para listar esses identificadores.

Atribuição por postagem exige identificadores nativos nos links gerados. Os
Sub_ids podem representar empresa, destino, publicação e regra usando IDs opacos,
sem dados pessoais. Links antigos sem essa identificação não devem receber
atribuição retroativa por aproximação de nome ou horário.

O Portal Shopee permite exportar o relatório de cliques. O Explorer público
consultado não possui uma operação de cliques totais. O clique que gerou uma
conversão não equivale à audiência total de uma publicação. Uma futura importação
deve partir de um arquivo oficial concreto e manter seu período e sua origem;
esta entrega não insere um redirecionador nos links existentes.

## Verificação

Os testes Python exercitam os limites de datas e fusos, campos indisponíveis,
sanitização do snapshot, pendências sem horário, autorização por empresa e
propagação correta de falhas. Os testes DOM exercitam consulta por período,
concorrência entre respostas, gráfico acessível, estados vazios e preservação de
dados. A revisão visual usa somente dados sintéticos e bloqueia escritas e
tráfego externo, nas larguras 320, 360, 430, 768 e 1280 pixels, em ambos os temas.

## Referências

- [Shopee Affiliate Open API Explorer Brasil](https://open-api.affiliate.shopee.com.br/explorer/v2)
- [Shopee: acompanhar cliques](https://help.shopee.com.br/portal/10/article/127718)
- [Shopee: acompanhar vendas e seus estados](https://help.shopee.com.br/portal/10/article/163052)
- [Shopee: Sub_ids](https://help.shopee.com.br/portal/10/article/127556)
- [Shopify: uso do dashboard de análise](https://help.shopify.com/en/manual/reports-and-analytics/shopify-reports/overview-dashboard/using-the-overview-dashboard)
- [PartnerStack: medir o desempenho do programa](https://support.partnerstack.com/hc/en-us/articles/18957035815315-Measuring-program-performance)
