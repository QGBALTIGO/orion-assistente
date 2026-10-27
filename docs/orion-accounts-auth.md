# Contas, convites e e-mail transacional do Orion

Referência do candidato de 27/10/2026. Este documento descreve o código e os
testes desta tarefa; não confirma publicação nem entrega de e-mail em produção.
O estado operacional e os critérios de liberação estão em
[Domínio e e-mail — operação](operations/dominio-email-20261027.md).

## Cadastro e entrada

Com `PANEL_REGISTRATION_ENABLED=1` e transporte de e-mail disponível,
`POST /auth/register` inicia um cadastro `member` pendente, com senha de
**8 a 128 caracteres** armazenada como hash Argon2id. A mesma transação grava
o código de confirmação e sua mensagem criptografada na fila. O login fica
bloqueado até a confirmação. A interface avança para “Confirme seu e-mail”,
no mesmo cartão e com os mesmos componentes da autenticação atual.

Criar uma conta não comprova a posse do endereço e não concede papel
administrativo, propriedade de empresa ou acesso aos dados de outra empresa.
`/auth/features` informa a disponibilidade: novos cadastros e recuperação
dependem da configuração completa do transporte. As contas anteriores à
migração 0039 têm `registration_pending=false`, sem exigir confirmação nova,
alterar senha, versão ou sessões. O admin legado também permanece preservado.

A sessão é opaca e armazenada no Redis. O cookie `__Host-orion_session` usa
`Secure`, `HttpOnly`, `SameSite=Strict` e `Path=/`, sem atributo `Domain`.
A duração é de 12 horas ou 30 dias com “Lembrar de mim”. Permissões e versão
da conta são conferidas no servidor; alterações de senha ou versão não são
substituídas por informações antigas do navegador.

## Confirmação explícita do e-mail

`POST /auth/verify/resend` solicita um código para uma conta existente ainda
não verificada. Não cria conta e funciona mesmo quando o cadastro público
está fechado. Abrir um convite não dispara esse pedido. Iniciar um cadastro
novo gera o primeiro código automaticamente; reenvios exigem clique explícito.
Retomar um cadastro com a senha correta não cria outra conta nem outro envio.

O código tem **6 dígitos**, expira em **10 minutos** e é de uso único. O banco
de tokens guarda HMAC-SHA256 com `PANEL_OTP_SECRET`, e não o código legível.
Cinco erros consomem o token; um novo pedido invalida os tokens anteriores da
mesma finalidade. Há limites adicionais por IP, destinatário e solicitação.

`POST /auth/verify` valida o código, preenche `verified_at` e conclui o cadastro
pendente. Essa confirmação preserva senha e sessões existentes, não abre uma
sessão automaticamente, não aceita convites e não concede vínculo com empresa.
`GET /auth/me` informa `email_verified` a partir do banco atual.

O conteúdo necessário ao envio, inclusive o código, permanece temporariamente
criptografado na outbox. Assim, a proteção HMAC da tabela de tokens não deve
ser confundida com ausência de conteúdo sensível em toda a infraestrutura.

## Recuperação de senha

“Esqueci minha senha” chama `POST /auth/password/forgot`. A resposta não revela
se a conta existe. Quando elegível, um código de 6 dígitos é enfileirado para o
endereço cadastrado, inclusive se a conta ainda não tiver confirmado o e-mail.
Aplicam-se a validade de 10 minutos, o limite de cinco erros e os limites de
solicitação; a resposta HTTP não comprova entrega na caixa de entrada.

`POST /auth/password/reset` exige o código e uma senha de 8 a 128 caracteres.
O sucesso confirma a posse do e-mail, altera o hash, consome os tokens pendentes
e incrementa `auth_version`, invalidando as sessões anteriores dessa conta.
Também registra um aviso transacional de senha alterada. O usuário entra de
novo; trocar apenas o domínio do painel não executa esse fluxo.

## Convite para a equipe

1. O proprietário autorizado cria um convite para sua empresa. Com e-mail e
   transporte habilitado, o envio entra na fila; sem e-mail, permanece o link
   manual. A API informa `email_delivery`, sem afirmar entrega.
2. O destinatário abre `/convite#token=...` e cria uma conta ou entra. O acesso
   inicial e a prévia não enviam mensagem nem aceitam o convite.
3. Se necessário, a própria página permite solicitar e informar o código de
   confirmação. Após verificar, ela consulta `/auth/me` novamente.
4. A pessoa clica explicitamente para solicitar o acesso. Uma conta não
   verificada permanece bloqueada. Quando o convite tem e-mail, ele deve
   corresponder ao da conta verificada.
5. O proprietário revisa a solicitação e aprova ou rejeita. Só a aprovação
   válida cria ou ativa o vínculo e as permissões da equipe.

O convite expira em sete dias. O token vai no fragmento do link; não deve
aparecer em logs, relatórios ou mensagens de erro. Repetir a criação com a
mesma chave de idempotência não gera outro token nem outro envio; o replay
retorna `link=null`. Confirmação de e-mail, aceite e aprovação são três ações
distintas. Essa revisão de equipe não cria uma fila de aprovação global para
o proprietário publicar o site da própria empresa.

## Administrador legado e permissões

O login administrativo legado continua usando sua credencial e seu mecanismo
de sessão existentes. A identidade `auth_provider=legacy_admin` mantém
**`email=NULL`**; não se inventa um endereço nem se converte essa identidade
em conta pessoal para permitir envios.

Contas comuns, suporte global e vínculo de proprietário são autorizações
distintas. Um papel global não equivale a propriedade arbitrária de empresa.
O fluxo separado de reivindicação de administrador, quando configurado, exige
e-mail reservado verificado e prova da senha administrativa legada; o cadastro
comum nunca faz essa promoção. As autorizações de publicação e de equipe
continuam sendo verificadas pelo backend.

## Fila persistente de e-mail

A API grava o evento e a outbox na mesma transação PostgreSQL. O worker ARQ
existente processa os envios com Redis para limites atômicos. Não há varredura
histórica que envie mensagens a todas as contas ou a convites antigos.

| Tipo | Evento |
| --- | --- |
| `verify` | Pedido explícito de confirmação de e-mail |
| `reset` | Pedido explícito de recuperação de senha |
| `password_changed` | Redefinição de senha concluída |
| `team_invite` | Novo convite de equipe com e-mail |
| `team_access_approved` | Aprovação de acesso de equipe |
| `team_access_revoked` | Revogação de acesso ou convite elegível |

`payload_enc` usa uma chave Fernet dedicada e estável; o destinatário de
controle usa HMAC. Uma chave única de evento impede duplicação na outbox.
As tentativas do mesmo item reutilizam o mesmo conteúdo e a mesma chave no
provedor. O worker confere se o token, convite ou vínculo ainda é atual antes
de enviar e remove o conteúdo criptografado nos estados terminais.

Os estados persistidos são `pending`, `accepted`, `dead`, `uncertain`,
`suppressed` e `expired`. A interface traduz esses estados para fila, aceite,
falha ou impedimento. **`accepted` significa aceito pelo provedor, não entregue
ou lido.** Não há comprovação de entrega por webhook nesta implementação.

Erros transitórios podem repetir a mesma solicitação com recuo e até oito
tentativas, respeitando a expiração do evento e uma janela máxima de 23 horas.
O Resend retém chaves de idempotência por 24 horas. Um item terminal
`uncertain` não é reenviado automaticamente; exige conferência do recibo antes
de qualquer decisão manual. [Idempotência no Resend](https://resend.com/docs/dashboard/emails/idempotency-keys).

## Configuração e limites

O transporte é `resend_https`, pela API HTTPS. Por compatibilidade, a chave
de envio continua em `PANEL_SMTP_PASSWORD`; isso não significa que o worker
use SMTP. `PANEL_SMTP_FROM` precisa usar remetente autorizado no provedor.

API e worker devem compartilhar `PANEL_PUBLIC_URL`, transporte, remetente,
credencial, `PANEL_OTP_SECRET` e `PANEL_EMAIL_FERNET_KEY`. O segredo OTP deve
ter pelo menos 32 caracteres e a chave Fernet deve ser válida. Mantenha ambos
estáveis entre reinícios e serviços; não gere outra chave a cada deploy.

O envio normal exige simultaneamente `PANEL_EMAIL_ENABLED=1` e
`PANEL_EMAIL_LIVE_SEND=1`, além da configuração válida. O exemplo versionado
mantém ambas em `0` e os segredos vazios. `PANEL_TEST_MODE=1` restringe os
envios e novos cadastros a `PANEL_TEST_EMAIL`.

O padrão do Orion é de 2 tentativas de envio por segundo, 100 por dia e 20
por destinatário por janela diária; pedidos possuem limites separados.
O plano Free do Resend informa limite de 100 e-mails por dia. São limites
operacionais, não qualificação para 10 mil usuários simultâneos ou campanhas.
[Limites do Resend](https://resend.com/pricing).

## Migração, preservação e evidências

A migração aditiva `0038_email_outbox`, sobre `0037_recurring_goals`, cria a
outbox com RLS e FORCE RLS, proprietário `app_migrator` e políticas restritas.
`app_support` tem apenas SELECT/INSERT/UPDATE; `app_runtime` e PUBLIC não
recebem acesso. Não há preenchimento retroativo nem envio provocado pela
migração. Contas, histórico, sessões, QR e IA existentes são preservados.

O runner `scripts/backup_and_migrate_email_outbox.py` exige revisão de origem,
papel restrito, trava, backup privado com proprietário e ACL preservados,
checksum e leitura de volta do objeto antes do upgrade. Depois valida revisão
e contratos antigos e novos. Se já estiver em 0038, apenas verifica o contrato.

A migração aditiva `0039_registration_confirmation` acrescenta o estado de
cadastro pendente, preservando todas as contas anteriores. O runner
`scripts/backup_and_migrate_registration.py` exige origem 0038, backup privado
verificado e papel restrito antes do upgrade. Reexecuções em 0039 apenas
verificam os contratos. Não reverter para código que ignore cadastros pendentes.

Os seis modelos de e-mail usam a logo oficial existente, renderizada como PNG
em `/brand/orion-email-logo.png`, com texto alternativo e cores do painel.
O PNG evita a incompatibilidade de SVG em clientes de e-mail. Mensagens já
enfileiradas mantêm seu conteúdo congelado para preservar a idempotência.
A restauração deve ser ensaiada em banco isolado; nunca sobrepor o banco ativo
para testar. A migração é somente para frente.

Nesta tarefa foram executados testes unitários, PostgreSQL isolado, HTTP com
worker real e provedor falso, restauração de dumps sintéticos e a jornada de
convite no Chromium e WebKit. Eles demonstram o percurso e a preservação em
ambiente isolado, sem e-mail real. Os recibos, limites dessa prova e etapas
pendentes estão no documento operacional.
