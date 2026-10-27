# Domínio e e-mail transacional — 27/10/2026

## Estado desta entrega

O candidato foi implementado e testado em ambiente isolado. No momento desta
redação, a configuração de e-mail foi preparada com as duas flags de envio
em `0`, **sem confirmar deploy desse candidato**. A publicação deve ser
comprovada pelos recibos da API e do worker, versão e verificações abaixo.
Configuração preparada não equivale a processo ativo com essa versão.

Na inspeção operacional desta tarefa, `orionagenda.pro` apresentou certificado
TLS válido, o domínio remetente apareceu verificado no Resend e foi preparada
chave restrita a envio nesse domínio. Esses fatos não demonstram entrega na
caixa de entrada. Um diagnóstico real, pontual e autorizado permanece
pendente; não há autorização implícita para envio de convites em massa,
campanhas ou testes com mensagens reais de WhatsApp.

O endereço `www.orionagenda.pro` permanece sem habilitação por limite do plano
de hospedagem atual. Não houve upgrade. Usar o domínio raiz; não anunciar
`www` como funcional nem adicioná-lo à lista de origens antes da provisão.

API, worker, PostgreSQL, Redis e WhatsApp continuam direcionados à Railway.
Esta entrega não muda volumes, cadastro administrativo, IA atual ou sessões
QR, e não reativa a VPS.

## Origens, sessão e Google

Configuração pública prevista, sem credenciais:

```text
PANEL_PUBLIC_URL=https://orionagenda.pro
PANEL_LEGACY_PUBLIC_ORIGINS=https://orion-assistente-production.up.railway.app,https://orion-painel-iota.vercel.app
GOOGLE_OAUTH_PUBLIC_ORIGIN=https://orion-assistente-production.up.railway.app
```

`PANEL_PUBLIC_URL` é uma origem HTTPS raiz, sem caminho, usuário, senha,
consulta ou fragmento. Ela alimenta os links de convite/recuperação, URLs dos
sites publicados e canonical/OG da landing. O servidor valida a configuração;
não deriva essa origem de um `Host` ou `X-Forwarded-Host` arbitrário.

A lista legada admite somente origens HTTPS exatas, no máximo oito. Ela mantém
compatibilidade explícita dos formulários públicos e da validação do callback
Google; não permite CORS irrestrito nem desativa a conferência de origem/CSRF
do painel. Links de prévia continuam privados e autenticados. O facade Vercel
existente é compatibilidade de acesso, não uma segunda instalação de dados.

O cookie de sessão é host-only: entrar em `orionagenda.pro` pode exigir novo
login. Isso não apaga a sessão já existente na Railway/Vercel. Não copiar
cookies entre hosts e não definir `Domain=.orionagenda.pro`. Preservar
`__Host-orion_session`, Secure/HttpOnly/SameSite=Strict e o armazenamento Redis.

Não trocar `GOOGLE_REDIRECT_URI`, cliente OAuth ou credenciais de integração
só para mudar `PANEL_PUBLIC_URL`. O callback já registrado permanece exatamente
na origem Railway com caminho `/integrations/google/callback`. O backend
informa `authorization_origin`; a interface abre essa origem antes de iniciar
o consentimento, mantendo o cookie nonce host-only do callback. Uma futura
troca do callback exige cadastro correspondente no Google e nova validação.

## E-mail: configuração privada da API e do worker

Ambos os serviços precisam dos mesmos valores válidos de origem pública,
transporte, remetente, credencial, segredo OTP e chave Fernet. Manter chaves
atuais estáveis; reiniciar ou publicar não deve gerar novos segredos.

| Variável | Condição |
| --- | --- |
| `PANEL_EMAIL_ENABLED` | `0` durante preparação; junto com LIVE_SEND controla o envio normal |
| `PANEL_EMAIL_LIVE_SEND` | `0` durante preparação; somente `1` com liberação correspondente |
| `PANEL_EMAIL_TRANSPORT` | `resend_https`, envio HTTPS na porta 443 |
| `PANEL_SMTP_PASSWORD` | Nome legado da chave API Resend restrita a envio; nunca registrar seu valor |
| `PANEL_SMTP_FROM` | Remetente autorizado no domínio verificado |
| `PANEL_OTP_SECRET` | Segredo estável com pelo menos 32 caracteres, compartilhado entre serviços |
| `PANEL_EMAIL_FERNET_KEY` | Chave Fernet dedicada válida e estável, compartilhada entre serviços |
| `PANEL_TEST_MODE` / `PANEL_TEST_EMAIL` | Piloto com destinatário controlado; endereço só no ambiente privado |
| `PANEL_REGISTRATION_ENABLED` | Decisão independente; cadastro imediato não envia código |
| `ORION_EMAIL_DELIVERY_PROBE` | `0` normalmente; diagnóstico separado, apenas uma execução autorizada |

O diagnóstico do preflight é uma via explícita separada da outbox: com
`ORION_EMAIL_DELIVERY_PROBE=1` pode enviar mesmo se as duas flags normais
estiverem em `0`. Reexecutá-lo cria outra solicitação. Planejar uma única
execução, registrar apenas resultado sanitizado e restaurar a flag para `0`.
O texto do diagnóstico ou um HTTP 200 não comprova chegada à caixa de entrada.

Rotacionar `PANEL_OTP_SECRET` invalida códigos pendentes e altera os digests de
controle. Rotacionar a Fernet sem tratar os itens existentes impede a leitura
do conteúdo da fila. Não trocar essas chaves como tentativa de corrigir DNS,
login ou envio. Não expor destinatários, códigos, tokens, payloads ou corpos
de resposta do provedor nos logs e relatórios.

## Comportamento e recuperação da fila

São seis eventos transacionais: confirmação solicitada (`verify`), recuperação
solicitada (`reset`), senha alterada (`password_changed`), convite novo
(`team_invite`), acesso aprovado (`team_access_approved`) e revogado
(`team_access_revoked`). Não há campanhas nem envio retroativo de contas ou
convites existentes. O cadastro continua imediato, com senha de 8 a 128
caracteres e sem envio automático. O admin legado permanece com e-mail nulo.

A API persiste evento e outbox na mesma transação. O conteúdo é criptografado,
o destinatário de controle usa HMAC e o evento tem chave única. O worker usa
trava transacional, confere a fonte atual e reutiliza conteúdo/chave do provedor
por item. Expiração de token, revogação ou substituição do evento podem impedir
o envio pendente sem desfazer a operação de conta ou equipe já válida.

`accepted` significa apenas aceito pelo provedor. Não existe confirmação de
entrega/leitura nem webhook de entregabilidade neste candidato. Erros
transitórios têm repetição limitada, com a mesma chave, até oito tentativas
e no máximo 23 horas a partir da primeira tentativa, respeitando o prazo do
evento. Um estado terminal `uncertain` exige reconciliação e nunca reenvio
automático. O provedor conserva as chaves por 24 horas; ultrapassar essa janela
e criar outra chave pode duplicar uma mensagem. [Idempotência do Resend](https://resend.com/docs/dashboard/emails/idempotency-keys).

## Migração 0037 → 0038 e recuperação

Usar o runner revisado `python -m scripts.backup_and_migrate_email_outbox`,
com credenciais privadas e papel de migração restrito. O encadeamento da
liberação deve concluir/verificar 0037 antes de executar esse último runner.
Não executar `alembic upgrade head` sem passar pelos controles de backup.

O runner só admite origem `0037_recurring_goals` ou destino
`0038_email_outbox`. Ele verifica o escopo da conexão, assume `app_migrator`,
obtém a trava de liberação e valida os contratos de publicação e metas já
existentes. Antes de alterar 0037, exige backup privado em formato custom com
proprietários/ACL preservados, valida o arquivo, calcula checksum e confere
o objeto remoto por metadados e leitura completa. Falha de backup, leitura,
origem ou contrato bloqueia a migração.

Depois do upgrade específico para 0038, confere novamente os contratos
anteriores e o novo: 18 colunas, constraints/FKs, índices, owner `app_migrator`,
RLS e FORCE RLS, duas políticas e privilégios efetivos. `app_support` recebe
somente SELECT/INSERT/UPDATE; runtime e PUBLIC não recebem acesso. Não pode
haver função ou trigger adicional que amplie acesso à outbox. Uma repetição
em 0038 somente verifica o contrato; não faz nova migração nem novo envio.

O ensaio desta tarefa usou dumps reais de PostgreSQL com **dados sintéticos**,
restauração em bancos separados e leitura de volta de armazenamento local
simulado; **não usou S3 real nem backup do banco de produção**. Comparou dados,
owners, ACL, políticas e RLS antes e depois. A primeira comparação estrita de
dois checks antigos rejeitou a representação equivalente de casts produzida
pelo `pg_restore`; o recibo foi preservado. A correção aceita somente as duas
representações exatas, sem ampliar valores/tipos/ACL. Os clones 0037 e 0038
restaurados passaram nos contratos finais.

Dez alterações deliberadas em clone adicional foram rejeitadas pelo guard,
incluindo owner, grants indevidos, RLS/FORCE, política, índice e valores de
tipo/status extras. Esses testes não alteraram as bases originais/restauradas.

A migração é somente para frente. Em falha operacional, interromper novos
envios e preferir correção para frente. Restauração do backup de produção é
procedimento de emergência separado: conter gravações, conferir revisão,
objetos privados, owners/ACL e compatibilidade do aplicativo, restaurar em
banco isolado e validar antes de qualquer troca autorizada. Não restaurar
sobre o banco ativo nem fazer downgrade automático para “testar rollback”.

## Evidências executadas

Artefatos locais em `orion-domain-email-20261027`, ao lado do checkout:

| Evidência | Resultado e limite |
| --- | --- |
| `email-backend-final.xml` | 135 testes, zero falhas; conjunto pode sobrepor outros recibos |
| `email-adversarial-final.xml` | 44 testes, zero falhas |
| `email-auth-regressions.xml` | 96 testes, zero falhas |
| `email-integration-final.xml` | 30 testes, zero falhas; preserva recibo anterior com uma falha corrigida |
| `email-rollout-guard.xml` | 48 testes, zero falhas |
| `email-migration-recovery-acceptance.json` | Backup/restauração sintéticos, sem S3 real; dados e segurança preservados |
| `email-release-contract-readonly.json` / `email-release-contract-drift-acceptance.json` | Contratos 0037/0038 e rejeição dos dez desvios em clone |
| `email-http-worker-e2e-acceptance.json` | HTTPS, PostgreSQL, Redis e worker reais locais; Resend falso, 13 aceites simulados dos seis tipos |
| `email-inline-invite-chromium/acceptance.json` / `email-inline-invite-webkit/acceptance.json` | Proprietário cria/revisa no painel; convidado confirma e aceita explicitamente; zero pedidos externos ou erros de página |
| `ui-final-corrected.tap` | 590 testes DOM, todos passaram; recibo anterior `ui-final.tap` preservado com a expectativa antiga de fallback de nome, corrigida somente no teste |

Nos navegadores, abrir a prévia gerou zero envios; solicitar código foi uma
ação explícita e confirmar não aceitou o convite automaticamente. Os códigos
e tokens não aparecem nos recibos. Essas provas usam contas sintéticas e
provedor falso; não demonstram entrega real, reputação do domínio ou carga
de produção. Não somar os conjuntos sobrepostos como testes exclusivos.

## Critérios antes de ativar

1. Confirmar commit/revisão efetivamente publicados em API e worker, health e
   recibo do runner 0038, incluindo backup privado real e checksum/readback.
2. Conferir contrato em produção somente em leitura, flags inicialmente `0`,
   segredos estáveis e iguais, origem HTTPS e callback Google preservado.
3. Conferir domínio raiz, certificados, login por host, canonical/OG, links
   de convite/recuperação, prévia privada e acesso às origens legadas. Não
   declarar `www` disponível sem resolver a limitação do plano.
4. Após as verificações anteriores e a liberação correspondente, habilitar as
   duas flags na API e no worker, conferir o novo processo e a fila vazia ou
   revisada. A configuração de piloto pode restringir o destinatário quando
   aplicável. Manter `ORION_EMAIL_DELIVERY_PROBE=0` para impedir sondagens.
5. Executar somente o diagnóstico real já autorizado, para destinatário
   controlado, uma vez, com marcador persistente e chave idempotente. Registrar
   aceite separado dos eventos do provedor e da chegada observada pelo
   destinatário. Conferir falhas, expiração e limites sem disparar convites
   reais como teste automático.

## Pausa e limites de capacidade

Para suspender o envio normal, colocar **`PANEL_EMAIL_ENABLED=0` e
`PANEL_EMAIL_LIVE_SEND=0` na API e no worker**, e manter
`ORION_EMAIL_DELIVERY_PROBE=0`. Não excluir fila, tokens, contas ou sessões.
Conferir os processos já ativos e recibos: uma mensagem já aceita pelo
provedor não pode ser desfeita. Ao reativar, itens pendentes ainda válidos
podem voltar a ser processados; revisar a fila primeiro. `uncertain` terminal
permanece bloqueado, sem reenvio automático.

O limite Free anunciado pelo Resend é de **100 e-mails por dia**. O Orion
mantém `PANEL_EMAIL_DAILY_LIMIT=100`, além de limites por segundo,
destinatário e solicitação; as tentativas consomem a cota do aplicativo.
Aumentar a variável não aumenta a cota contratada do provedor.
[Plano e limites atuais](https://resend.com/pricing).

Esta entrega não qualifica 10 mil usuários simultâneos, mil conexões WhatsApp
nem grandes volumes de e-mail. Faltam orçamento/cota compatíveis, teste de
carga e concorrência com critérios mensuráveis, monitoração da fila e
entregabilidade real. Campanhas continuam fora do escopo desta ativação.
