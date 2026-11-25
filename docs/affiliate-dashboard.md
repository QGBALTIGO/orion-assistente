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

## Relatórios reais da conta Shopee

A integração usa a credencial já cifrada no serviço de Afiliados na VPS.
`conversionReport` foi autorizado e validado com a conta conectada. O Dashboard
agora mostra vendas geradas, pedidos, itens e comissão bruta estimada, além de
séries diárias e produtos. O escopo é **todos os links da conta Shopee**.
Resultados sem Sub_id conhecido não são atribuídos a uma publicação do Orion.

`actualAmount` e `itemTotalCommission` são somados uma vez por linha elegível,
com `Decimal`, sem multiplicar novamente pela quantidade. O total é convertido
para centavos no final. O valor de reembolso não é descontado pela segunda vez:
a semântica de `actualAmount` líquido de reembolso não está documentada.
Pedidos pendentes e concluídos podem compor a estimativa; cancelados, não pagos,
itens cancelados e fraude ficam fora. Valores ausentes ou que não conciliam
continuam desconhecidos. A comissão dos itens é reconciliada com a conversão;
divergência torna o relatório parcial, sem rateio inventado do teto.

Concluído não significa comissão validada ou paga. A comissão validada permanece
`null`, pois o relatório validado exige um identificador específico que a API
pública não lista. Cliques também permanecem indisponíveis: o campo `clickTime`
só identifica o clique que converteu, e não existe operação pública de total de
cliques no schema consultado. Uma importação futura depende de um arquivo real
exportado pelo Portal de Afiliados.

O histórico de publicações tem agora `published_at` persistido no recebimento
da confirmação. A migração aditiva recupera o instante dos registros já
publicados: a revisão do worker comprovou que esse estado é terminal e que
`changed` era escrito no recebimento da confirmação. Os totais e a série diária
consultam o histórico completo, com índice por empresa e data. A fila e as
falhas usam os totais atuais; cartões recentes continuam limitados a 120.

### Sincronização e atribuição

A consulta inicial aguarda até oito segundos. Se precisar de mais tempo,
retorna a operação e o estado de sincronização; a interface faz consultas
limitadas até o relatório ficar pronto. O cache dura cinco minutos, por empresa,
fuso e versão da credencial. Períodos compartilham a mesma janela de cobertura.
Uma nova sequência completa de páginas substitui o snapshot, atualizando
cancelamentos e reembolsos sem duplicar conversões. Falhas preservam o último
snapshot confirmado e sua data, com espera de 90 segundos antes de repetir.
A interface identifica dados em atualização, parciais ou com falha.

Os publicadores Telegram e PromoTech passam um Sub_id opaco por job, usando o
link nativo da Shopee. `longLink` devolveu o `utm_content` desse identificador na
validação real; guardamos o valor exato retornado, sem reconstruir separadores.
O mapeamento financeiro exige igualdade exata, mesma empresa e versão de
credencial. Links antigos e links cuja atribuição não possa ser confirmada
continuam sem atribuição por destino. A autorização para enviar mensagens não
é alterada por esta integração; os testes usam transportes simulados.

## Consulta e isolamento

`GET /api/v1/afiliados/{tenant_id}/api/analytics?period=month`

O acesso exige a mesma identidade verificada de proprietário, empresa ativa e
nicho Afiliados utilizada pelo restante do módulo. A consulta ao provedor é
assinada para a empresa, o método e o caminho específicos. O fuso é obtido do cadastro da empresa e encaminhado ao serviço. O parâmetro de período
aceita somente `today`, `7d`, `30d` ou `month`.

O proxy tenta obter `/api/analytics` no serviço de Afiliados. Apenas a ausência
da capacidade (404, 405 ou 501) utiliza o builder operacional. Erros de permissão,
limite de chamadas e falhas do serviço não são transformados em relatórios vazios.
O fallback consulta `/api/state`, resolve o fuso cadastrado da empresa e devolve
somente os campos explicitamente permitidos. As respostas usam `no-store`.

Os períodos são datas locais inclusivas: sete dias incluem hoje e os seis dias
anteriores; trinta dias incluem hoje e os 29 anteriores. `month` começa no dia 1
do mês local. A fila atual é independente desse período. O fallback para uma versão antiga
do serviço continua identificando sua amostra de até 120 registros.

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

## Contratos e limites da Shopee

O Explorer oficial brasileiro foi consultado em 25/11/2026. A operação
`conversionReport` permite consultar conversões, pedidos, itens, valores e
comissões. A implementação usa as credenciais existentes no serviço de
Afiliados; a permissão para relatórios foi confirmada em uma consulta real. Uma conexão de catálogo
validada não comprova, por si só, acesso ao relatório financeiro.

A sincronização deduplica conversões, pedidos e itens e substitui os snapshots
para refletir cancelamentos/reembolsos; a comissão por item é conciliada com
a conversão sem repetir o total por pedido.
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
o fluxo usa os links nativos gerados pela Shopee.

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

## Implantação e recuperação

O código do piloto foi recuperado da versão ativa
`b6ecf0f66e3e549529e7dd15fe50fa739a624b3d` e incluído neste repositório.
A atualização da VPS copia essa versão e aplica os arquivos revisados do piloto
mais `app/affiliate_analytics.py`, módulo puro compartilhado do contrato.
O procedimento guarda um backup consistente SQLite, verifica hashes da base e
da versão nova, compila e importa o app com a virtualenv da VPS antes de mudar
o diretório do serviço. Só a API do piloto é reiniciada; o processo WhatsApp
mantém a sessão existente. Em falha, o diretório anterior é restaurado; as
adições compatíveis de schema são preservadas, evitando reverter atividade da fila.

A imagem da API Orion deriva da imagem de produção observada no momento do
build, com verificação de hashes dos arquivos de Afiliados substituídos.
O shell geral `orion-suite.js` permanece o da base publicada.
