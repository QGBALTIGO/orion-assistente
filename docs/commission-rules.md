# Comissões por profissional e serviço — Orion

A regra antiga permanece o padrão para toda a base: **percentual geral**.
A mudança é opcional e não altera nenhuma comissão já registrada.

## Modos

- **Percentual geral**: todos os atendimentos concluídos usam o percentual
  cadastrado na ficha do profissional (sobre a receita efetiva após descontos).
- **Por serviço**: todo serviço marcado na ficha deve ter explicitamente uma
  taxa percentual de 0–100%, ou valor fixo em reais por execução.
  A regra é por **profissional × serviço**, não uma taxa universal do produto.
- Comissões fixas são limitadas ao valor líquido atribuído ao serviço; um
  desconto que zera esse serviço também zera a comissão fixa. Não são
  promessas de salário incondicional. A interface avisa o estabelecimento.
- O custo é reconhecido **somente na conclusão**; marcar falta ou cancelar
  não gera comissão. Valores “a partir de” exigem valor final validado pela
  equipe, como já acontecia antes desta mudança.

## Combos, adicionais e descontos

Pacotes e seleções têm sua composição original imutavelmente guardada em
`appointment_items.catalog_snapshot`. Para fins de comissão, o motor abre
seus componentes e divide a receita **real** líquida proporcionalmente
aos preços históricos, com distribuição inteira determinística dos centavos.
Nunca usa o preço que o catálogo passou a cobrar depois da reserva.
Se faltar a composição histórica ou a regra de um serviço, a finalização
por serviço é **bloqueada** para revisão, em vez de inventar uma comissão.

Exemplo de um atendimento de R$ 150,00:
- Corte: R$ 100,00, comissão 10% → R$ 10,00
- Barba: R$ 50,00, comissão fixa R$ 10,00 → R$ 10,00
- Custo total de comissão: R$ 20,00

Se o atendimento tiver desconto, a base de cada serviço diminui
proporcionalmente; a comissão percentual incide sobre sua parte líquida.

## Imutabilidade, segurança e financeiro

Toda conclusão salva `commission_mode_snapshot`,
`commission_percent_snapshot` (somente no modo geral),
`commission_cents` e `commission_breakdown` de modo atômico.
A mesma comissão salva alimenta Financeiro, DRE, Desempenho, saldo
acumulado, adiantamentos, quitações e histórico. Trocar o modelo ou
o percentual não muda valores de atendimentos já finalizados.

`professionals.commission_plan_version` protege alterações concorrentes.
`commission_rules` guarda taxas antigas mesmo se uma vinculação de
serviço for retirada depois, preservando o fechamento de reservas em
andamento. A atualização dos vínculos e regras acontece numa única
transação, sob bloqueio de profissional e RLS por empresa; os perfis de
leitura não podem alterá-las.

Previsões de comissões futuras no modo por serviço não são exibidas
como valores garantidos; dependem da composição e do valor efetivamente
realizado. A receita estimada e o dinheiro recebido permanecem separados.

## Migração e publicação

A migração `0033_service_commissions` é aditiva e define
`commission_mode='percent'` para todos os profissionais existentes.
Comissões históricas continuam intactas. A mudança deve ser aplicada
antes de disponibilizar novas gravações; executar após conferência de
backup e da versão anterior `0032_variable_service_prices`.

A rotina `scripts.apply_commission_migration` usa credenciais
temporárias do banco migrador e garante a evolução **0032 → 0033**.
Depois, devolver o comando normal de pré-deploy e limpar as referências
temporárias. API e worker devem publicar o mesmo SHA. Não reinicializar
dados nem recalcular quitações realizadas.
