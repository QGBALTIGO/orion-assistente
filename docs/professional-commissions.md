# Comissão profissional por serviço — mudança controlada

## Objetivo
Cada profissional pode usar **percentual padrão** (comportamento já existente) ou
**por serviço**, com taxa percentual ou valor fixo em centavos por serviço.
As configurações pertencem ao profissional e à empresa. Não há regra global
que substitua a comissão de outro profissional.

## Regras de negócio
- **Padrão (%):** incide sobre o total líquido concluído; o arredondamento
  continua sendo realizado sobre o atendimento todo, como antes.
- **Por serviço:** cada serviço-base selecionado precisa de uma regra % ou R$.
  Percentuais incidem sobre a parcela líquida daquele serviço (após descontos
  concedidos ao cliente). Valor fixo é um custo por ocorrência do serviço,
  não um percentual da venda; descontos não reduzem o valor fixo.
- **Preço variável ("a partir de"):** ao concluir, a diferença entre o mínimo
  cotado e o preço final informado é atribuída aos itens variáveis (com rateio
  entre itens variáveis quando houver mais de um), sem inflar o preço de serviços
  fixos presentes no mesmo atendimento.
- **Combos e adicionais gerados:** os itens sem regra explícita utilizam o
  percentual de reserva do profissional, identificado como default no snapshot.
  A tela explica essa exceção.
- Uma comissão fixa pode superar o valor da venda; isso significa margem
  negativa e não é cortado silenciosamente.
- Ausência/cancelamento não gera comissão. Finalização automática usa a mesma
  função transacional que a conclusão manual. Preços variáveis ainda exigem
  confirmação manual do preço final.
- Comissão, regra aplicada, base, tipo e valor por item são fotografados no
  atendimento concluído. Alterar o cadastro posteriormente não altera
  nenhuma comissão já registrada.
- Financeiro/Desempenho/quitações/DRE usam a comissão gravada (snapshot).
  Recebível gerado na conclusão não equivale a pagamento recebido. Quitações e
  vales não são novamente descontados do resultado na DRE.

## Segurança / dados
- professional_commission_rules tem FK composta por empresa, RLS forçada
  e grants restritos. O cliente não envia ID da empresa no corpo das regras.
- O salvamento da configuração e dos vínculos é atômico, autorizado pelo papel
  owner/editor, com CAS em commission_config_version e lock de agendamento
  para impedir alteração durante a conclusão.
- Cadastros legados não conseguem mudar o modo de comissão diretamente.
- As operações usam centavos inteiros, Decimal e rateio exato, sem perda
  cumulativa de centavos. Uma tentativa repetida de criar o mesmo profissional
  tem chave de idempotência.
- Snapshots de quitação continuam imutáveis e o cálculo de vales/adiantamentos
  permanece vinculado ao mesmo saldo acumulado de comissões.

## Publicação (obrigatória; não efetuada por este PR)
1. Executar testes unitários, suíte CI e avaliação de interface Safari/iPhone.
   Validar uma empresa de teste com regras % e R$, desconto, preço variável,
   multi-serviço, quitação e troca de configuração.
2. Fazer backup verificável do PostgreSQL, testar restauração fora de produção
   e confirmar a revisão atual 0032_variable_service_prices.
3. No ambiente de migração previamente autorizado, com MIGRATION_DATABASE_URL,
   ORION_EXPECTED_DATABASE_HOST, ORION_EXPECTED_DATABASE_NAME e role app_migrator,
   executar uma única vez:
   python -m scripts.apply_professional_commission_migration
   O script falha se a origem não for exatamente 0032.
4. Confirmar que a versão é 0033_professional_commissions, tabela/grants/RLS
   estão corretos e só então atualizar a API/painel para este commit.
5. Verificar login, criação/edição de funcionário, agendamento, finalização,
   comissão, DRE, projeções, vales, quitação e isolamento entre empresas.

**Atenção:** scripts.start_api não aplica migrações automaticamente.
Não fazer deploy desse código antes do upgrade do banco — se a API consultar
as colunas novas sem a migração, haverá falha nas requisições. A branch
de feature/PR foi escolhida para evitar atualização antecipada da produção.

## Limites de compatibilidade
Os atendimentos concluídos muito antigos sem commission_cents já eram
estimados pela porcentagem atual do cadastro; esta mudança não os reescreve
nem inventa registros retroativos. Os relatórios continuam a identificá-los
como estimativas históricas. O "saldo de caixa" de lançamentos pagos é diferente
do "resultado líquido" por competência na DRE.
