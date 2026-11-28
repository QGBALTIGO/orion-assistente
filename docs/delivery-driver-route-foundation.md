# Orion Entregas — fase 1: reservas e conferência

Base Git: cb02ce983b6a27a586ff162377c9eab122c8967a. A imagem de produção deve ser conciliada antes da publicação.
Este commit não ativa o piloto nem altera dados reais.

- Migração 0118 amplia as atribuições existentes, com estado reservado e identificação/ordem da saída.
- Flag adicional `ORION_DELIVERY_ROUTE_V2_TENANTS` (lista explícita de UUIDs), vazia por padrão. A autorização existente de Delivery e o vínculo verificado do entregador continuam obrigatórios.
- Reservas livres e aceite de ofertas mantêm o pedido comercial confirmado. O despacho ocorre somente depois da conferência física por variante, usando a transição existente.
- Uma transação para o lote: revisão otimista, bloqueio por empresa, validação de propriedade, idempotência e rollback integral. Endereços completos e contatos não são exibidos na fila livre.
- Limite piloto de cinco pedidos simultâneos; não representa capacidade física. Não adicionar peso fictício nem aceitar trânsito de mercadoria não conferida.
- Admin pode cancelar ou liberar uma reserva antes do despacho. Revogar vínculo libera reservas não despachadas. Operações de drenagem das reservas continuam possíveis mesmo após desligar a flag.
- Estoque comercial permanece reservado até a conclusão, sem baixa dupla. O evento de saída registra a conferência da carga; o estoque em trânsito completo e as devoluções físicas exigem a fase seguinte.
- Portal mantém login e Pix manual. Sugestão inicial agrupa por bairro e antiguidade, sem inventar GPS/quilometragem. Interface piloto mostra fila, seleção e conferência; não é ainda a interface completa do protótipo.
- Teste de concorrência, isolamento, quantidade incorreta, idempotência, despacho e baixa única em PostgreSQL.
- Próximas fases: reconciliação da imagem e CI, UI mobile completa/mode entrega, histórico de rotas, capacidade real, entregas parciais, caixa, eventos WhatsApp, offline protegido.

## Publicação
Validar a migração e todos os testes numa base isolada; comparar os arquivos alterados com a imagem publicada e construir sobre a base imutável correta. Não ligar o piloto em produção sem homologação. Não reverter a migração caso haja registros de reservas ou rotas. Para contenção: desligar novas reservas e drenar os pedidos existentes com a versão compatível; preservar o banco e os outros nichos.

## Interface e release v2
A interface usa Figtree/Outfit e os componentes visuais Orion. Navegação em cinco áreas, modo entrega com avanço, filtro e seleção, sequência manual e agrupamento por bairro, conferência, histórico individual, pagamentos mistos sem Pix e tema escuro/alto contraste. O caixa é uma consulta de recebimentos confirmados dos últimos 7 dias (até 100 pedidos), não uma conciliação de dinheiro em espécie. O modo offline permanece somente leitura da tela em memória; ações críticas exigem servidor. O roteamento não calcula distância rodoviária; não há capacidade de peso cadastrada. A flag aceita também @preview, sempre limitada aos tenants já autorizados no piloto Delivery. O script de publicação usa a imagem imutável atualmente ativa e a migração existente com backup privado, preservando os demais nichos.
