# Horário excepcional compartilhado

No painel da empresa ou na área do profissional, escolha **Horário excepcional**
e marque **Permitir dois clientes neste horário**. A opção começa desmarcada,
vale apenas para esse agendamento e é apagada ao trocar o tipo de registro.
No profissional, a revisão mostra a autorização antes de confirmar.

- Cada cliente tem protocolo, serviços, duração, preço, recebimento, comissão e
  notificações próprios. A autorização e o responsável ficam no histórico.
- No máximo dois clientes simultâneos por profissional, incluindo preparação
  e limpeza. Interseções parciais contam; intervalos adjacentes não se somam.
- Uma reserva temporária do site em confirmação não pode ser sobreposta.
  Aguarde confirmar ou expirar. Bloqueios e folgas continuam impedindo reservas.
- Agendamentos normais, recorrentes, do site e WhatsApp continuam exigindo
  horário livre. Seus contratos não aceitam a autorização excepcional.
- O site bloqueia a união dos períodos ocupados, inclusive quando só resta o
  agendamento compartilhado. Cancelar um cliente libera somente seu período.
- A lista e a grade mantêm os dois registros separados. A grade usa as colunas
  existentes para eventos simultâneos. Remarcação exige horário livre; para
  compartilhar outro período, crie um novo excepcional com autorização explícita.
- Adicionais podem ampliar uma exceção compartilhada se não causarem três
  clientes simultâneos. O fluxo existente permite preservar o horário reservado.

O motor usa o bloqueio transacional já existente por empresa e profissional.
A migração 0081 mantém a exclusão GiST dos agendamentos comuns e adiciona um
gatilho que também protege escritas diretas contra capacidade excedida.
O trabalhador e a API verificam o contrato do esquema em modo somente leitura.

## Publicação

O fluxo `orion-exceptional-overlap.yml` valida PostgreSQL 16 isolado, os dois
painéis em DOM, Chromium/WebKit e regressões de recebimentos. Somente após os
testes gera uma imagem imutável baseada na imagem publicada de recebimentos.
A pré-publicação `python -m scripts.backup_and_migrate_exceptional_overlap`
confere o banco esperado, gera e verifica backup privado, migra de 0080 para
0081 e verifica as restrições. API e worker iniciam apenas com o contrato pronto.
O rollback de esquema falha se houver exceções compartilhadas ativas;
nunca exclui ou cancela clientes automaticamente.
