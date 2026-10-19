# Login e avisos transacionais da Agenda

Esta entrega é isolada da evolução de onboarding da PR59. Parte de `dee6aaac`
(migração 0019). Não habilita marketing, respostas automáticas a novos contatos,
QR multiempresa, cobrança ou novas permissões de equipe.

## Identidade visual

`orion-auth.css`, carregado depois de `orion-suite.css`, usa os mesmos tokens
`--os-*` do painel, Figtree para controles/texto e Outfit para títulos. Login,
cadastro e recuperação respondem ao tema salvo em `orion-theme`. Não há uma
paleta alternativa turquesa. As animações usam transições curtas e respeitam a
preferência de movimento reduzido. Os formulários preservam autenticação,
OTP, permissões, recuperação, autocomplete e a escolha de manter a sessão.

O roteiro `tests/visual/auth_preview.py` renderiza os arquivos reais do painel,
com assets locais incorporados e respostas HTTP sintéticas, em 320, 390, 820 e
1366 pixels, claro e escuro. Confere rolagem horizontal, fontes, tokens computados,
troca de telas e gera capturas. Não simula entrega de e-mail nem login real.

## Agenda e WhatsApp

Confirmação, remarcação, cancelamento, finalização e falta registrados pelo
painel criam uma notificação na mesma transação do agendamento. O site público
usa o mesmo caminho. Falhar ao gravar o aviso reverte a mutação; uma falha externa
posterior não apaga o agendamento. Não há chamada de rede durante a gravação.
Alterações solicitadas pelo próprio cliente no WhatsApp já têm a resposta do
atendimento e não recebem uma segunda cópia por esta fila. Elas invalidam avisos
anteriores que ainda não foram enviados. Eventos antigos não são reenviados.

O aviso contém empresa, horário, profissional e protocolo. Remarcação informa
horário anterior e novo. Notas, motivos internos, detalhes clínicos, valores e
dados de operadores não são enviados. Finalizar não significa pagamento recebido.

Há um vínculo explícito entre a empresa e o número QR instalado. O worker e a
bridge conferem empresa, conta conectada e destinatário. Uma mudança de telefone
do cliente impede enviar ao contato antigo. Empresas sem esse canal permanecem
com aviso pendente visível; não usam a conta de outra empresa. Esta entrega não
transforma a bridge legada em uma instalação multiempresa.

O worker usa a mesma trava do profissional para conferir versão e impedir que
um aviso de horário antigo ultrapasse uma alteração simultânea. A chamada de
transporte tem limite de dez segundos; nesse intervalo uma alteração concorrente
desse profissional pode aguardar a liberação da trava. A capacidade de envio
existente da bridge continua valendo, sem remover seus limites.

### Estados observáveis

| Estado | Significado |
|---|---|
| Aguardando envio | Intenção registrada no PostgreSQL, ainda sem envio. |
| Aguardando conexão | Não foi possível iniciar o envio. Tentativas com espera progressiva. |
| Verificando envio | Uma tentativa foi assumida; ainda não há resposta conclusiva. |
| Enviado ao WhatsApp | Transporte aceitou a mensagem, sem comprovar entrega ao aparelho. |
| Entregue / Lido | Recibo correspondente recebido do WhatsApp. Leitura pode não ser fornecida. |
| Envio sem confirmação | Resultado incerto; consulta-se o registro, sem enviar nova cópia automaticamente. |
| Substituído | Uma atualização tornou obsoleto o aviso ainda não enviado. |
| Prazo encerrado / não realizado | Aviso expirado, destinatário inválido ou alterado. |

A confirmação e a remarcação expiram até o início do atendimento; os demais
avisos têm prazo de 24 horas. Desconexão provoca novas verificações até esse prazo,
com espera progressiva até 15 minutos. A consulta de recibos é feita separadamente.
Uma resposta HTTP perdida não é evidência de que uma mensagem não foi enviada.
Por isso, estados incertos não aceitam reenvio automático nem o botão de retry.
O botão de tentar novamente só alcança tentativas comprovadamente não iniciadas.

A bridge grava um identificador cifrado e sincroniza o arquivo antes de qualquer
relay. O registro fica em `/data/orion-appointment-notices`, fora da pasta rotativa
de autenticação. Conserva identificadores emitidos para não duplicar depois de
reiniciar; ao atingir 50 mil registros ou detectar corrupção, bloqueia novas
tentativas e informa necessidade de manutenção. Não remove registros de deduplicação
silenciosamente. A chave deriva do token interno existente; preserve esse token.
A retenção do conteúdo e do retry de mensagens segue o mecanismo cifrado já existente.

## Migração e implantação manual

`0020_appointment_notices` cria somente a nova tabela com RLS obrigatória,
restrições, índices e grants mínimos. Nenhum agendamento existente muda e nenhum
aviso histórico é fabricado. O runner selado confere PostgreSQL 16, banco esperado,
papel `app_migrator`, revisão exata e assinatura do schema. Executa DDL e revisão
em uma transação; ensaios conferem rollback e preservação. Replay é só leitura.
Esta operação aditiva não cria um novo backup completo do banco.

Publicar API, worker e QR bridge com o mesmo commit. Preservar `/data`, credenciais,
allowlists de IA, domínio, região, réplicas e demais flags existentes.

API, pre-deploy:

```sh
python -m scripts.one_time_notice_migration && python -m scripts.probe_qr_bridge
```

Manter as identidades e credenciais de migração já configuradas. Definir
`ORION_RUN_NOTICE_MIGRATION=1`, `ORION_MIGRATION_SWITCH_ROLE=1` e desligar
`ORION_RUN_AGENDA_MIGRATION=0`. O launcher remove as credenciais de migração
antes de iniciar o processo HTTP.

Worker, pre-deploy:

```sh
python -m scripts.wait_for_notice_schema
```

Esse gate lê apenas o catálogo como `app_runtime` por no máximo 90 segundos.
Não migra o banco. Configurar no worker as referências às mesmas variáveis
`QR_AI_TENANT_ID`, `QR_BRIDGE_URL` e `QR_BRIDGE_TOKEN` da API. A bridge precisa
da referência a `QR_AI_TENANT_ID`. API, worker e bridge devem ter
`ORION_APPOINTMENT_NOTICES_ENABLED=1` somente na publicação revisada.

Se o worker concluir o build antes da API, espera o schema. Se a API ou worker
chegar antes da bridge, a fila permanece aguardando o canal compatível. Nada é
reenviado como tentativa cega durante a troca de versões. A fase de staging não
executa essas operações; a publicação depende de Deploy pelo proprietário.

Após a publicação, conferir uma alteração autorizada em uma reserva de teste,
seu histórico e o recibo do WhatsApp. Os testes de código não comprovam a entrega
legível em um aparelho real nem capacidade para 1.500 empresas ou sessões.
