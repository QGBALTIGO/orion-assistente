# Recebimentos de atendimentos

Configuração por empresa em **Configurações → Financeiro → Recebimentos de atendimentos**.
O padrão é confirmação manual. Apenas proprietário, administrador ou suporte pode alterar a regra;
editores podem registrar recebimentos e leitores apenas consultar.

O modo automático dá baixa após 24 horas corridas do término previsto, respeitando duração
realizada ou composição dos serviços quando maior. O worker verifica os prazos no banco a cada
minuto. Não há processo aguardando 24 horas em memória. Reinícios recuperam prazos vencidos.

Cada nova conclusão gera no máximo um recebível automático. Um lançamento já vinculado não é
sobrescrito. Recebimentos antigos e registros retroativos não são inscritos silenciosamente.
Desativar ou reativar a configuração invalida as decisões agendadas na versão anterior.

**Manter pendente** suspende a baixa daquele lançamento. Retomar é uma ação explícita e pode
causar baixa na próxima verificação se o prazo já venceu. Corrigir uma baixa para pendente também
suspende o automático. Mudanças e confirmações preservam histórico, identidade do operador,
origem, valor, forma de pagamento e data, sem alterar a data original do serviço.

A confirmação automática é escrituração por regra do estabelecimento. Não comprova recebimento
bancário e não inventa Pix, cartão ou outro meio. O caixa usa a data de recebimento; desempenho,
metas, DRE de serviços e geração de comissão continuam usando a conclusão do atendimento.
Não há segunda venda ou comissão ao registrar o pagamento. O filtro **A receber** inclui todos
os meses e informa que os indicadores superiores continuam mostrando o mês selecionado.

O lançamento e sua versão são conferidos sob bloqueio. Confirmações repetidas têm recibos de
idempotência. Cancelamento, falta, alteração de valor, exceção manual, desligamento da regra e
outro lançamento ativo vinculado impedem a baixa automática. Não afeta assinaturas, despesas,
vales ou pagamentos de comissão. Pagamentos parciais/sinais exigem uma etapa própria posterior.

## Publicação

Migração aditiva `0080_service_receipts`, após `0079_service_archive`. A pré-publicação da API
executa `python -m scripts.backup_and_migrate_service_receipts`: confere o mesmo banco de runtime,
guarda e verifica backup privado com propriedade e privilégios, aplica a revisão e valida o
contrato. API e worker usam verificações de esquema somente leitura antes de iniciar.

Publicar a API antes do worker. A imagem deriva da imagem imutável que já estava em produção,
preservando dependências e os demais módulos. Não remover a revisão para reverter o recurso:
voltar ao modo manual suspende novas baixas e mantém a trilha financeira.
