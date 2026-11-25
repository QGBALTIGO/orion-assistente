# Ciclo de planos de Barbearia

O catálogo mantém Essencial, Profissional e Elite por R$ 29,90, R$ 89,90 e R$ 139,90 a cada 30 dias. Os limites são 2, 6 e 10 profissionais. A liberação manual usa o mesmo controle de acesso, com vigências de 1, 3, 6 ou 12 meses de calendário no fuso da empresa. A confirmação administrativa grava o responsável, a operação, a vigência e o plano. As liberações antigas sem data são identificadas na ficha e continuam legíveis até revisão individual.

A renovação manual do mesmo plano acrescenta tempo ao vencimento vigente. A troca para um plano maior conserva o saldo de vigência. A troca para um plano menor começa ao fim do período contratado. O painel apresenta o excesso de profissionais e o sistema preserva os cadastros. Uma compra em andamento ou um período pago vigente exige regularização antes de nova liberação manual.

A confirmação do checkout continua vinculada ao pedido, à assinatura, à oferta e à empresa. A próxima cobrança só aparece quando vem de uma leitura validada do contrato. Para troca de oferta, o painel orienta a consulta ou o suporte. A API pública da Cakto documenta cancelamento e alteração de forma de pagamento e descreve a atualização direta de assinatura como indisponível. A quantidade de dias que o cliente já pagou permanece disponível durante a regularização, salvo retenção financeira comprovada. [Contrato da Cakto](https://docs.cakto.com.br/api-reference/subscriptions/update)

## Condições de publicação

- Compare o digest da API, do worker e da ponte QR com `deploy/orion-plan-cycle/sources.json`. O empacotamento verifica cada arquivo que será substituído.
- Use `python -m scripts.backup_and_migrate_plan_cycle --audit` no ambiente autorizado para obter a versão, a elegibilidade da migração e o inventário agregado. A versão anterior aprovada é `0093_dental_reception`.
- Configure `python -m scripts.backup_and_migrate_plan_cycle` como pré-publicação da API. O comando usa o destino privado já configurado para fazer e verificar a cópia antes de aplicar as revisões `0094` a `0097`.
- Atualize API e worker juntos antes de habilitar `ORION_BARBER_PLAN_CYCLE`. Habilite o teste em `ORION_BARBER_SELF_SERVICE_TRIAL` somente quando `ORION_TRIAL_IDENTITY_KEY` estiver provisionada de forma persistente. Essa chave mantém a associação protegida com o e-mail confirmado.
- Qualifique separadamente a ponte QR e o canal administrativo primário verificado antes de habilitar `ORION_WELCOME_WHATSAPP` na API, no worker e na ponte. O canal de suporte é consultado no momento do clique. O e-mail alternativo é informado em `ORION_SUPPORT_FALLBACK_EMAIL`.
- Confira o evento `refund_requested` nas entregas das ofertas recorrentes e do Pix avulso. Verifique também as leituras de pedidos, assinaturas e disputas. A qualificação deve usar dados sintéticos e um ambiente autorizado de teste. [Eventos da Cakto](https://docs.cakto.com.br/conceitos/webhooks)

As quatro revisões adicionam registros de elegibilidade, consentimento, períodos manuais e verificação financeira. Elas não reescrevem cadastros, sessões, assinaturas, profissionais ou pedidos existentes. Caso seja necessário interromper uma publicação inicial, mantenha as migrações e pause a habilitação. Após as primeiras reduções programadas ou retenções financeiras, a API e o worker precisam continuar usando a leitura dessas tabelas para preservar os direitos.

## Pontos de revisão

- A documentação pública da Cakto descreve o reembolso do pedido e não comprova um valor parcial para aplicar automaticamente. Casos ambíguos exigem revisão.
- A identificação antiga apagada antes da migração só pode ser vinculada ao benefício se existir prova independente. O histórico dos testes existentes permanece por empresa e conta.
- Compras ou mensagens simuladas confirmam as transições internas. A validação do recebimento pelo provedor exige homologação dele.
