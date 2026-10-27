# Logo dos e-mails e confirmação do cadastro — 27/10/2026

Este documento registra o candidato testado. A publicação efetiva deve ser
conferida pelos recibos de API/worker e do banco, sem confundir teste sintético
com envio ou criação de conta real.

## Comportamento

Os seis modelos transacionais usam a logo oficial já existente, convertida de
`orion-brand-light.svg` para PNG sem redesenho. O arquivo público permitido é
`/brand/orion-email-logo.png`, também incluído no pacote da aplicação. O e-mail
usa logo com texto alternativo, botão azul, fundo claro e cores do painel.
O PNG é compatível com os clientes que não exibem SVG. O renderizador de
desenvolvimento é `scripts/render_email_logo.py`; não executa na API.

Criar conta inicia um cadastro pendente e grava código/mensagem na mesma
transação. A pessoa confirma o código na segunda etapa do mesmo formulário
antes de entrar. O endereço aparece inteiro, com quebra de linha; nome,
senha e código não são guardados no armazenamento do navegador. A senha no
banco já é Argon2id. A confirmação termina na tela Entrar, sem login automático.

O código tem seis dígitos, validade de dez minutos, cinco tentativas e uso
único. O botão de reenvio respeita espera de sessenta segundos e os limites
do servidor. Retomar com a senha correta preserva o cadastro original e não
envia outro e-mail automaticamente. Trocar o endereço exige voltar à etapa
anterior. Erro ou expiração mantém a etapa de confirmação disponível.

## Preservação e segurança

A migração aditiva `0039_registration_confirmation`, sobre 0038, acrescenta
`registration_pending NOT NULL DEFAULT false`. Portanto, contas e sessões
anteriores permanecem válidas, inclusive contas antigas sem e-mail confirmado
e o admin legado sem endereço pessoal. Nenhuma conta anterior é convertida
em pendente. O novo estado só admite conta pessoal `member` ainda não verificada.

Login, resolução de sessão e resolução de vínculo recusam contas pendentes.
Validar e-mail ou recuperar senha com código válido conclui o cadastro.
Isso não concede empresa, papel administrativo nem aceite de convite.
As regras existentes de autorização, proprietário e revisão de equipe continuam.

Os segredos OTP/Fernet e a chave de envio não mudam. Mensagens já enfileiradas
mantêm conteúdo/chave congelados; não reescrever HTML pendente para aplicar a
logo nem reenviar mensagens antigas. Não existe disparo retroativo.

## Liberação protegida

O runner `scripts/backup_and_migrate_registration.py` só admite 0038/0039,
verifica os contratos anteriores, obtém trava, exige backup privado com
owners/ACL, checksum e leitura de volta e só então aplica 0039 como
`app_migrator`. O proprietário de backup pode exportar as tabelas com FORCE RLS;
a credencial de runtime não ganha essa capacidade. Reexecução em 0039 apenas
valida. A migração é para frente: não publicar versão que ignore pendentes.

Antes de publicar, comparar dados e identidade do QR em leitura, registrar o
commit e exigir os testes aprovados. Depois, conferir o mesmo commit na API
e worker, marcador do backup/migração, contrato do banco, arquivos públicos,
cadastro disponível, sessão QR e flags anteriores. Não testar criando usuário,
redefinindo senha ou enviando e-mail real em produção.

## Evidências desta tarefa

Artefatos em `orion-signup-brand-20261027`, ao lado do checkout:

- `backend-final.xml`: 98 testes passaram, incluindo PostgreSQL real isolado,
  confirmação, erros, reenvio, atomicidade e acesso antigo preservado.
- `auth-dom-final-reviewed.tap`: 24 testes de autenticação/convite passaram.
- `ui-final-reviewed.tap`: suíte final com 595 testes aprovados, após a revisão
  de legibilidade e disponibilidade. O recibo anterior de 593 testes foi mantido.
- `signup-browser-acceptance.json`: quatro jornadas completas, Chromium/WebKit,
  claro/escuro, larguras 320/390/1280, HTTPS/API/PG/Arq reais e Resend simulado.
  O cadastro fica bloqueado antes do código, código inválido não conclui, o
  código correto permite login `member` e a logo PNG carrega pelo endpoint real.
- `migration-acceptance.json`: dumps/restaurações reais de bases sintéticas 0038
  e 0039, S3 simulado, dados/owners/ACL/RLS preservados, default incorreto recusado
  e reexecução sem backup ou DDL. Não foi restaurado banco ativo.

Os testes não enviam e-mail real, não acessam destinatários reais e não testam
WhatsApp/campanhas. O teste de interface legado que esperava navegação direta
ignorando a configuração da empresa já estava desatualizado no commit anterior;
foi alinhado ao caminho existente, sem mudar a autorização do produto.

Esta mudança não aumenta cotas de envio e não qualifica a capacidade futura de
dez mil usuários simultâneos. Caixa de entrada, spam e leitura em aparelho real
não são comprovados pelos testes simulados.
