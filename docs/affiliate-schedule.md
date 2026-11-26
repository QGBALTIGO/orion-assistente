# Correção de cadência das automações de afiliados

## Escopo
Motor de publicação da VPS, não o worker geral da Railway. Baseia-se no
código revisado de Afiliados (inclui o editor de imagens previamente testado).
O estado e commit ativos da VPS ainda precisam ser conferidos por operador
autorizado antes de aplicar qualquer atualização.

- Preparação em até três tarefas concorrentes, com faixa independente de
  publicação serial. Sem aumentar concorrência de envio no WhatsApp/Telegram.
- As requisições Shopee da mesma conta continuam serializadas e compartilham
  o cooldown informado pelo provedor. Nenhuma rotação de chaves ou bypass.
- Próxima ocorrência calculada a partir da programação, não do atraso do
  processamento. Não disparar em rajada para compensar janelas perdidas.
- Seleção prévia exclui itens repetidos por destino antes de gastar chamadas
  de links/IA; a checagem transacional do enqueue continua valendo.
- A UI sinaliza quando o limite diário é incompatível com a janela. Motor
  novo permite até 144 oportunidades por dia; 07:00–21:00 com 10 minutos
  comporta 84. A ação "Usar limite da janela" é uma escolha explícita,
  não sobrescreve automaticamente os limites de regras cadastradas.
- Preserva autorização, janela, repetição, preço confirmado, permissão
  administrativa do destino, limites de conexão e confirmação de entrega.

## Publicação
Conferir versão ativa, configuração dos grupos e histórico de decisões no
motor VPS. Preservar o SQLite, sessões WhatsApp e demais serviços. A implantação
deve ser feita pelo procedimento app-only já existente, a partir de um release
revisado sobre a versão efetivamente instalada. A flag de capacidade precisa
estar presente no estado autenticado antes de liberar o campo novo da UI.
Não enviar ofertas de teste aos grupos reais sem autorização específica.
Validar com destinos de teste, medição de atraso e respostas dos provedores.
Não prometer envio a cada 10 minutos se não houver oferta elegível ou se a
conexão/provedor estiver limitando o serviço.
