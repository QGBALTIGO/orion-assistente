# Orion — Combo com preço “A partir de” (revisão 0034)

## Regra de negócio
- Se **qualquer serviço-base do combo** tiver `price_mode='from'`, o combo herda automaticamente o modo `from`. Não há segundo seletor manual que possa contradizer os componentes.
- O preço promocional cadastrado no combo permanece o **mínimo estimado**, desde que positivo e não superior à soma dos mínimos dos serviços. Em um combo previamente gratuito que passe a conter serviço variável, o valor mínimo exibido é a soma dos mínimos dos componentes, nunca R$ 0,00.
- Quando todos os componentes voltarem a preço fixo, o combo volta automaticamente a `fixed`.
- Mudanças dos serviços acionam os triggers de `refresh_service_package`. Combos não ficam desativados apenas por conter preço variável. Inatividade de componentes, profissionais incompatíveis, horários e limites continuam impedindo a reserva.
- Site, painel, WhatsApp/QR, cotação, confirmação e agendamento usam o `price_mode` derivado do serviço sintético.
- Na finalização, valores variáveis **não entram automaticamente como receitas**. A equipe deve informar o valor final acordado, que não pode ser inferior ao mínimo da reserva. O recibo financeiro é registrado somente com o valor confirmado.
- Reservas já realizadas continuam com seu `price_mode_snapshot`, preço mínimo e histórico intactos, ainda que a tabela de preços mude.
- A migração 0034 substitui somente a função de atualização de pacotes; mantém permissões e triggers anteriores e revisa os combos existentes com componente variável.

## Liberação Railway
1. Testar migração em PostgreSQL isolado (0033 -> 0034), inclusão/edição de combo, mutação posterior de serviço, volta a preço fixo, desconto, modalidade variável, cotação, WhatsApp, RLS e finanças.
2. Preservar o bucket privado existente de backup e conferir suas referências. A ferramenta de migração aborta caso versão origem, host, banco, bucket ou credenciais não sejam os esperados.
3. Na publicação, usar **apenas uma execução** de `python -m scripts.backup_and_migrate_combo_pricing` na etapa pré-deploy da API. O script faz `pg_dump` formato customizado, valida `pg_restore --list`, envia ao bucket privado, confere hash SHA-256 por leitura integral remota, executa a migração e verifica versão final. Se algo falhar, a API antiga continua ativa.
4. Atualizar API e worker no mesmo commit validado, verificar `/health`, status Railway, requisições 5xx e finalizações agendadas. Remover o pre-deploy temporário depois; não deixar migração executando em cada nova publicação.
5. Registrar localização privada do backup, sem expor conteúdos ou chaves em logs públicos. Restaurar somente em ambiente de teste isolado, não sobre produção.

O build da imagem não executa migrations por conta própria. **Nunca publicar esta versão da API antes de instalar a revisão 0034.**