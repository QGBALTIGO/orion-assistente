# Delivery e Distribuição — acompanhamento da implementação
Atualizado em 27 de novembro de 2026. Responsáveis pela operação comercial devem conseguir identificar se um recurso foi publicado, se está em homologação ou se ainda depende de desenvolvimento.

## Infraestrutura e reutilização

| Componente | Constatação | Decisão |
| --- | --- | --- |
| GitHub e distribuição | A revisão `0a654220` gerou a imagem implantada no projeto Orion. As branches de homologação e de empacotamento são distintas. | Avançar na homologação. O empacotamento do overlay e o deploy Railway são operações separadas. |
| Railway | No projeto principal existe o ambiente production com API, worker, bridge de QR, PostgreSQL e Redis. O último deploy consultado de cada serviço constava como SUCCESS. | API e painel de Delivery usam o serviço existente. O cadastro de empresa exige vínculo pessoal e o servidor consulta o opt-in do tenant. |
| Capacidade observada | Treze amostras de cinco em cinco minutos em uma hora. Memória média em GB: API 0,503, worker 0,126, bridge 0,156, Redis 0,008, PostgreSQL 0,225. A API atingiu 0,915 GB. | Atribuição por nicho exige medição de uso por tenant. Essa amostra não estima a fatura mensal. |
| Vercel | O projeto orion-painel tem publicação de produção READY. O painel servido ao usuário foi validado antes desta etapa pelo domínio orionagenda.pro. | Estes arquivos de Delivery pertencem à imagem da API. Não há necessidade de uma segunda publicação do painel. |
| Identidade | Conta pessoal, membership, regra de uma empresa por conta, revisão de onboarding e bloqueio por plano existentes. | Usar tenant_transaction, políticas RLS e permissão no servidor para cada operação administrativa. Separar credencial do entregador e credencial pública de acompanhamento. |
| Pedido e estoque | commercial_quotes, commercial_orders, commercial_inventory, reservas e eventos já atendem loja e operador. | Um único motor. Pedido guarda fotografia de preço e endereço. Revisões impedem atualização perdida. |
| Caixa e cobrança | O Financeiro registra recebimentos, baixa parcial de fiado e estorno. billing_api.py tem cliente Asaas sandbox de assinaturas. prepaid_pix_api.py e Cakto também tratam planos da Orion. | A venda do estabelecimento precisa de credencial, cobrança e reconciliação próprias por empresa. Nunca usar as chaves de assinatura para recebê-la. |
| Atendimento | worker.py usa Redis, processa inbound_events, inbox, outbox e retentativas. Há integração Cloud API, QR e modelos de IA. | Central e transferência humana podem ser aproveitadas. O agente de vendas exige escopo de catálogo, autorização do canal, idempotência e ferramentas vinculadas ao mesmo tenant e comprador. |
| Apresentação | Painel server-rendered com módulos JS, CSS e componentes da suíte. Loja pública tem renderização e controle de exposição próprios. | Preservar as rotas existentes, edição sem recarga e temas. Manter histórico e endereços privados, inclusive após o cliente digitar telefone em loja pública. |

## Etapas e critérios de aceite

| Fase | Estado verificável | Trabalho para aceite comercial |
| --- | --- | --- |
| 1. Fundação | Nicho e cinco atividades, fluxo de conta e permissões de avaliação existentes. Auditoria inicial de código e infraestrutura nesta revisão. | Onboarding operacional em sete etapas, perfis granulares e comprovação regulatória. |
| 2. Gestão comercial | Catálogo, estoque, vasilhames e CRM na versão de avaliação. Em homologação entram endereços nomeados, regras por bairro e recebimento de compras de fornecedores com entrada física e despesa. | Ordens de reposição, devolução ao fornecedor, perdas, inventário por local/veículo, caução e geocobertura. |
| 3. Pedidos | Balcão, retirada, entrega, checkout opt-in, reserva atômica, instruções de troco, pagamento misto, visualização em lista e Kanban, repetição de pedido pelo operador e código privado de acompanhamento. | Estados independentes completos, edição após reserva e histórico do consumidor com verificação de identidade. |
| 4. Atendimento e pagamentos | Recebimentos manuais e vendas a prazo operacionais. | Credenciais sandbox da distribuidora, webhook real, conciliação, eventos fora de ordem, expiração da reserva, adaptadores, notificações e atendimento autorizado. |
| 5. Logística | Cobertura por bairro, agenda, despacho manual, solicitação de aceite, recusa, chegada, conclusão e ausência. Área do entregador com vínculo a e-mail verificado, expediente e compartilhamento voluntário da última posição em primeiro plano. | Carga por veículo, despacho automático com elegibilidade, mapa agregado, sequência otimizada de paradas e acerto financeiro de turno. |
| 6. Gestão avançada | Dashboard operacional diário e relatórios. No desempenho de vendas há Hoje, Ontem, 7, 30, 90 dias e datas personalizadas, com horas locais e comparação ao intervalo anterior. | Fechamento de turno, acerto do entregador, margem com custos históricos, recompra, fidelização e campanhas com consentimento. |
| 7. Comercialização | Reutilizar planos, liberação manual com vencimento e Cakto. | Aprovar preço após medição. NF-e e NFC-e dependem do emitente, UF, credenciamento, certificados e ensaios. |
| 8. Validação | A última revisão publicada tem ensaio PostgreSQL/Redis, navegador e regressão. A nova revisão segue o workflow de homologação. | Dois cenários integrais com app do motorista, comprovante de PSP sandbox, estoque, financeiro, histórico, rastreamento e regressão. |

## Alterações de banco e operação

- O acompanhamento público da loja distingue espera por despacho, solicitação de aceite, rota, chegada ao endereço e entrega. O código privado expira trinta dias após a última atualização operacional ou o agendamento, o que ocorrer por último. O retorno público contém apenas o andamento daquele pedido, seu total, pagamento e eventual agendamento.

- `0115_delivery_driver_portal` guarda autorização individual, expediente, última posição voluntária e atribuições por empresa e pedido. Em Logística, o proprietário vincula o e-mail verificado ao entregador e obtém o endereço do aplicativo. O vínculo não cria participação no painel administrativo. Em Pedidos, o operador escolhe o despacho manual ou solicita aceite a um entregador disponível. A oferta expira em cinco minutos. O aceite registra a entrada em rota. Na conclusão, valor, estoque e vasilhames usam a transação do pedido. A posição só é enviada com consentimento e a página visível. O botão de parada e o encerramento do expediente apagam a coordenada. A tela sem conexão solicita reconexão. Pedidos e dados de clientes não são gravados no cache offline.
- Compras, pagamento misto, desempenho e Kanban passaram na [homologação 46052214299](https://github.com/QGBALTIGO/orion-assistente/actions/runs/46052214299) e no [ensaio da imagem 46053162055](https://github.com/QGBALTIGO/orion-assistente/actions/runs/46053162055).

- Pedidos tem lista e Kanban com paginação independente para cada estado operacional. Repetir um pedido abre um novo carrinho com produtos e quantidades, mantém a escolha explícita de endereço e vasilhames e exige nova cotação e confirmação antes de reservar.

- O Dashboard usa o fuso da empresa e eventos persistidos de criação, conclusão, cancelamento, devolução e recebimento. O desempenho conserva o faturamento bruto na data da conclusão e mostra o estorno na data do movimento. O ticket médio só aparece quando o período tem conclusão. A troca de período atualiza o gráfico e seus indicadores no mesmo painel.

- A conclusão do pedido aceita até quatro meios recebidos. O operador confirma a divisão entre dinheiro, Pix conferido manualmente, débito e crédito. O servidor exige a soma exata e registra cada valor no Financeiro com o respectivo meio. O relatório contabiliza um único pedido. A devolução considera todas as parcelas recebidas. Essa alteração usa a estrutura existente de vínculos de recebimentos e dispensa outra migration.

- `0114_delivery_purchases` liga fornecedores por empresa, documentos de entrada, itens e despesas do Financeiro. O proprietário registra quantidades e custos de cada variação, uma data de vencimento ou o meio já pago. O recebimento grava estoque e despesa na mesma transação. Outra tentativa com o mesmo documento não cria uma segunda despesa. A baixa de uma conta a pagar usa o Financeiro. O custo efetivo da compra fica no documento, e o cadastro mantém o custo de referência do produto.
- A primeira imagem com endereços, regras por bairro, consulta ANP e novos campos do PDV passou na [homologação 46049683833](https://github.com/QGBALTIGO/orion-assistente/actions/runs/46049683833) e no [empacotamento 46049848694](https://github.com/QGBALTIGO/orion-assistente/actions/runs/46049848694). Esses registros demonstram o resultado do CI e da montagem da imagem.

- `0111_delivery_customer_addresses` adiciona endereços nomeados com FK composta até o cliente, RLS forçada e revisões. O operador escolhe e confirma o endereço no pedido. A exclusão apaga o conteúdo pessoal, conserva o identificador contra reenvio tardio e preserva a fotografia dos pedidos anteriores.
- `0112_delivery_area_terms` armazena as condições adicionais em coluna separada, preservando o formato do documento de cobertura das imagens anteriores. O orçamento usa o subtotal real dos produtos. O checkout recalcula sob a reserva de estoque. Retirada continua independente de bairro. O prazo é informado pela distribuidora.
- Publicação desta etapa começa pela homologação com banco sintético. Antes de um retorno a uma imagem sem os novos cálculos, pausar o checkout das lojas que usam mínimo ou gratuidade por bairro, conferir pedidos já confirmados e retomar após validar a política aplicável.
- O teste da fase 2 cobre dois nomes por cliente, concorrência na gravação, atualização com versão obsoleta, exclusão repetida, RLS e preservação do endereço histórico. O teste da loja compara o orçamento público e interno, limite inclusivo de gratuidade e o valor aplicado ao pedido.

## Referências operacionais

As referências comerciais abaixo descrevem processos anunciados pelos fornecedores. A navegação autenticada e a venda de teste foram feitas no FazPedidoPro. O pedido de teste #1001 recebeu confirmação, mas não foi localizado nas filas após a atualização. Este ponto impede usar aquela conta como evidência de recebimento ou despacho bem-sucedido.

| Decisão de produto | Referências |
| --- | --- |
| Conferir cliente, endereço, itens, casco, cobrança e expedição antes do fechamento, e permitir atendimento humano | [Telesys](https://www.telesys.com.br/), [System Gas](https://systemgas.com.br/), [Delivery Integrado](https://www.deliveryintegrado.com.br/), [FazPedidoPro](https://fazpedido.pro/faq/) |
| Separar encomenda, caixa, inventário, distribuição e consumo recorrente. O estoque do veículo deve participar da seleção do motorista | [HBX](https://hbxsystem.com.br/sistema-para-revenda-de-gas), [GasNow](https://gasnow.com.br/), [RevGás](https://site.revgas.com/), [Revenda Gás](https://www.revendagas.com/) |
| Preservar contexto, endereço confirmado e pausa de IA por atendente na mesma conversa | [PopGás](https://www.popgas.com.br/), [Kevia](https://kevia.com.br/), [Igne Chat, hoje BERA](https://site.igne.app.br/), [Cenvia](https://cenvia.com.br/), [PedeBot](https://pedebot.com.br/para/agua-e-gas/), [Pingow](https://pingow.com.br/) |
| Confirmar endereço antes de apresentar oferta, retirada ou entrega, condição e preço. Exibir somente acompanhamento disponível | [Ultragaz](https://retira.ultragaz.com.br/gas/app/), [AppGás](https://appgas.com/), [Preço do Gás](https://www.precodogas.com.br/termocliente.html), [UpGás](https://www.upgas.com.br/), [iFood](https://institucional.ifood.com.br/ajuda/como-funciona-a-entrega-do-ifood/), [Zé Delivery](https://ze-delivery-abi.zendesk.com/hc/pt-br/articles/47079472824465-Como-fa%C3%A7o-um-pedido-no-Z%C3%A9) |
| Separar disponibilidade, oferta de tarefa, aceite, execução e prova de entrega. Informar horário da última posição. Planejar paradas considerando janelas e capacidade | [Onfleet](https://onfleet.com/), [Tookan](https://jungleworks.com/tookan/), [Bringg](https://www.bringg.com/), [Spoke](https://spoke.com/dispatch), [Locus](https://locus.sh/), [eLogii](https://elogii.com/), [Fleetbase](https://fleetbase.io/), [Hikyaku](https://www.hikyaku.org/docs/dispatch-and-operations) |

## Tecnologias examinadas

### Incrementos em homologação

O painel de Logística oferece consulta ao cadastro GLP da ANP para o proprietário de uma empresa de Gás e Água. A consulta usa o CNPJ armazenado em Configurações, confere os identificadores de todos os registros e mostra fonte cadastral, data, autorização, vínculo, classe e situação informada pelo SIGAF. Uma resposta inesperada ou indisponível não comprova a situação do estabelecimento. O teste de contrato usa o formato oficial com transporte HTTP controlado. O acesso externo ao Swagger retornou 403 nesta sessão, portanto a integração ao vivo continua pendente de verificação.

A migração `0113_delivery_order_intake` acrescenta ao pedido interno origem, forma combinada, troco e observação. Balcão, telefone e atendimento humano podem ser identificados no PDV. Loja pública é determinada pela compra na vitrine e pedidos históricos internos aparecem como Painel. A origem pode ser filtrada no relatório e no CSV. No despacho o operador vê o troco ou a necessidade de maquininha. A baixa financeira exige o recebimento efetivo. Foram acrescentados modelos editáveis de P20, água de 5 L e regulador, sem preencher preço ou estoque.

O campo de atividade é a data do último push retornado pelo GitHub, não uma avaliação da qualidade ou da continuidade de manutenção. Esta revisão não incorpora código desses projetos. Para inclusão futura, revisar tag, dependências transitivas, licença e avisos do artefato efetivamente distribuído.

| Repositório | Último push consultado | Identificação da licença | Aplicação e dependências |
| --- | --- | --- | --- |
| [fleetbase/fleetbase](https://github.com/fleetbase/fleetbase) | 2026-11-27 | AGPL-3.0 | Referência do despacho e portais. Avaliar o serviço e suas obrigações antes de qualquer integração. |
| [fleetbase/navigator-app](https://github.com/fleetbase/navigator-app) | 2026-11-27 | AGPL-3.0 | Referência de app React Native com jornada, GPS e pedidos atribuídos. |
| [traccar/traccar](https://github.com/traccar/traccar) | 2026-11-27 | Apache-2.0 | Servidor Java e protocolos GPS. Candidato a serviço separado quando houver aparelhos e política de retenção. |
| [enatega/food-delivery-multivendor](https://github.com/enatega/food-delivery-multivendor) | 2026-11-11 | MIT | Apps como referência. Backend e API proprietários segundo o README. |
| [VROOM-Project/vroom](https://github.com/VROOM-Project/vroom) | 2026-11-27 | BSD-2-Clause | Otimização de tarefas, veículos e janelas sobre matriz de viagem. Não fornece telemetria. |
| [Project-OSRM/osrm-backend](https://github.com/Project-OSRM/osrm-backend) | 2026-11-27 | BSD-2-Clause | Motor C++ para rotas e matriz; preparar extrato regional e medir memória e atualização. |
| [valhalla/valhalla](https://github.com/valhalla/valhalla) | 2026-11-26 | ver arquivo de licença | Alternativa C++ multimodal. Verificar os avisos por componente na versão escolhida. |
| [osm-search/Nominatim](https://github.com/osm-search/Nominatim) | 2026-11-27 | GPL-3.0 | Geocodificação. O serviço público tem limite agregado de uso e restrição a busca automática. |
| [maplibre/maplibre-gl-js](https://github.com/maplibre/maplibre-gl-js) | 2026-11-27 | ver arquivo de licença | Renderização WebGL. Avisos de licença BSD e de terceiros. Exige fonte de tiles e atribuição. |
| [postgis/postgis](https://github.com/postgis/postgis) | 2026-11-27 | GPL-2.0 | Extensão PostgreSQL para polígonos e consultas espaciais. Medir volume e disponibilidade antes de habilitar. |
| [frappe/erpnext](https://github.com/frappe/erpnext) | 2026-11-27 | GPL-3.0 | Referência de estoque por local e contabilidade. Instalar outro ERP duplicaria cadastros. |
| [odoo/odoo](https://github.com/odoo/odoo) | 2026-11-27 | ver arquivo de licença | Community LGPLv3 e dependências com termos próprios. Enterprise separada. |
| [medusajs/medusa](https://github.com/medusajs/medusa) | 2026-11-27 | ver arquivo de licença | Carrinho, pedidos e pagamentos modulares. MIT no núcleo e materiais Enterprise com licença própria. |
| [vendure-ecommerce/vendure](https://github.com/vendurehq/vendure) | redirecionado | ver arquivo de licença | Repositório atual vendurehq/vendure. NestJS e GraphQL. Community GPLv3 e alternativa comercial. |
| [chatwoot/chatwoot](https://github.com/chatwoot/chatwoot) | 2026-11-27 | ver arquivo de licença | Central multicanal. Diretório enterprise com termos próprios. Orion já possui inbox e handoff. |
| [taskforcesh/bullmq](https://github.com/taskforcesh/bullmq) | 2026-11-27 | MIT | Fila Redis. A Orion já usa trabalhadores Python e Redis, dispensando uma segunda fila. |
| [google/or-tools](https://github.com/google/or-tools) | 2026-11-27 | Apache-2.0 | Alternativa de otimização com restrições. Comparar qualidade e custo com VROOM. |
| [openstreetmap/openstreetmap-website](https://github.com/openstreetmap/openstreetmap-website) | 2026-11-27 | GPL-2.0 | Código do site e base de dados possuem termos distintos. Os dados são ODbL. |

O [OpenStreetMap](https://www.openstreetmap.org/copyright) exige atribuição ao usar seus dados. O [Nominatim público](https://operations.osmfoundation.org/policies/nominatim/) impõe limite agregado e restringe automação. Um volume comercial precisa de dimensionamento e política adequados ao fornecedor. Uma matriz de rotas deve reportar origem, instante e indisponibilidade. Coordenadas observadas não podem ser substituídas por posições simuladas.

## Pagamentos, canais e conformidade

A primeira opção de ensaio é Asaas sandbox por estabelecimento. O endpoint deve autenticar o provedor, persistir a identidade do evento, consultar a cobrança no contexto da conta vinculada e reconciliar moeda, valor, pedido e beneficiário antes de liberar despacho. Aplicar reserva, event outbox e baixa numa transação. Uma cobrança recebida depois de esgotado o estoque exige reconciliação e resolução explícita. A avaliação dos demais provedores está no nível de documentação, sem transação ou credenciamento. [Asaas](https://docs.asaas.com/docs/receba-eventos-do-asaas-no-seu-endpoint-de-webhook) · [Mercado Pago](https://www.mercadopago.com.br/developers/pt/docs/your-integrations/notifications/webhooks) · [Efí](https://dev.efipay.com.br/docs/api-pix/webhooks/) · [Woovi](https://developers.woovi.com/) · [Pagar.me](https://docs.pagar.me/) · [PagBank](https://developer.pagbank.com.br/). O portal técnico da Stone consultado retornou erro 502.

A ANP identifica a Resolução 958/2023 para revenda e a 953/2023 para transporte motorizado de recipientes. A API oficial aceita CNPJ e informa autorização, classe, vínculo e situação SIGAF. O registro precisa ter fonte, data e conferência do responsável. Um CNPJ com dígitos válidos não comprova autorização. A regra de veículo e carga requer conferência da norma aplicável. A emissão fiscal só pode ser ativada após validação do estabelecimento e da UF. [ANP](https://www.gov.br/anp/pt-br/acesso-a-informacao/perguntas-frequentes) · [Manual da API de Revendedores](https://www.gov.br/anp/pt-br/centrais-de-conteudo/paineis-dinamicos-da-anp/paineis-dinamicos-do-abastecimento/api-revendedores-manual-usuario.pdf) · [Portal NF-e](https://www.nfe.fazenda.gov.br/portal/)

A política da Meta inclui bens e materiais perigosos entre os itens restritos. A venda de GLP pelo WhatsApp ficará desativada até obtenção da confirmação formal exigida. Para produtos elegíveis, a Cloud API documenta solicitações de localização por mensagem interativa. A autorização é por canal, produto, conta e finalidade. Emergências interrompem a intenção comercial e exigem orientação oficial e transferência humana. [WhatsApp](https://business.whatsapp.com/policy) · [Mensagens interativas](https://developers.facebook.com/docs/whatsapp/guides/interactive-messages/) · [Corpo de Bombeiros de MS](https://www.bombeiros.ms.gov.br/fale-conosco/perguntas-frequentes/)

## Custos e liberação

Sem novo serviço provisionado, assinatura ou compra externa nesta etapa. O custo incremental fixo de novos serviços é zero. Consumo do ambiente existente e CI permanecem medidos pelos provedores. Para formar preço, separar custo por pedido, tokens de IA e áudio, templates entregues, chamadas de mapas/rotas, retenção de posições, tarifas do PSP e horas de suporte. O total por plano depende do volume e do contrato de cada provedor. Não há base para prometer margem ou assinar contratos com os preços sugeridos.
