# Autenticação Orion 2.0 — cadastro, e-mail, senha e permissões

## Experiência visual

A rota pública `/panel` mantém a autenticação por sessão HTTP segura.
No desktop, mostra duas colunas (gradiente turquesa e formulário azul-marinho).
No tablet/celular, a coluna visual torna-se um cabeçalho compacto.
O CSS responde a `prefers-color-scheme: light/dark` e escolhe a versão
de logo apropriada via `picture`. O ZIP oficial do Orion é a referência
de formas e paleta; os vetores publicados em `app/static/orion-*.svg` incluem
o símbolo da marca e wordmark vetorizado com legenda tipográfica.
A mudança Entrar/Criar conta/Esqueci senha usa animação leve; usuários com
`prefers-reduced-motion` recebem transições desativadas.

## Regras de contas

- **Cadastro comum**: nome, e-mail, senha de 12 a 128 caracteres; grava
  no PostgreSQL, papel inicial `member`, e envia confirmação SMTP.
  Não concede acesso ao WhatsApp, cadastros de clientes ou ferramentas internas.
- **Confirmação de e-mail**: código randômico criptograficamente forte;
  grava apenas SHA-256 no PostgreSQL; expira em 24h e pode ser usado uma vez.
  A conta só pode logar depois dessa confirmação.
- **Senha**: hash Argon2id; redefinição com link expirando em 20 min e
  uso único. Um reset invalida todas as sessões da conta pela versão no banco.
- **Lembrar de mim**: 30 dias quando marcado; 12h caso contrário.
  Cookies `__Host-`, `Secure`, `HttpOnly` e `SameSite=Strict`.
- **Administrador proprietário**: o e-mail reservado é configurado apenas na
  variável `PANEL_OWNER_EMAIL`. O cadastro inicial **nunca** promove ninguém
  automaticamente. Após provar posse do e-mail com link de confirmação,
  a pessoa deve entrar e comprovar também a senha administrativa legada
  na tela de vinculação de permissões. A sessão anterior é invalidada após
  a promoção; é necessário login novamente.
- **Admin legado**: login atual permanece funcional. Desativá-lo só depois
  de uma migração e recuperação proprietária validadas.

## Configuração necessária para cadastro/recuperação

Por segurança, cadastro público permanece desabilitado se não houver e-mail
operacional configurado. Adicionar em Railway no serviço da API:

```text
PANEL_PUBLIC_URL=https://<dominio-oficial-do-orion>
PANEL_OWNER_EMAIL=<email-do-proprietario-confirmado>
PANEL_EMAIL_ENABLED=1
PANEL_REGISTRATION_ENABLED=1
PANEL_SMTP_HOST=<servidor-smtp-transacional>
PANEL_SMTP_PORT=465
PANEL_SMTP_USERNAME=<usuario-smtp>
PANEL_SMTP_PASSWORD=<senha-smtp>
PANEL_SMTP_FROM=Orion <no-reply@dominio-verificado>
```

O provedor deve autorizar envio pelo domínio escolhido (SPF/DKIM/DMARC).
Não é recomendável usar a senha comum de uma caixa pessoal como SMTP.
Os segredos acima **nunca** entram no repositório. O envio usa SMTP com
verificação TLS obrigatória (porta 465) ou STARTTLS (587).
`/auth/features` indica quais fluxos podem ser usados; o formulário não
simula envios quando o servidor não está configurado.

## Persistência e migração

A migration `0008_panel_accounts` adiciona `panel_accounts` e
`panel_email_tokens`, com RLS FORCE e acesso concedido apenas ao
papel `app_support`. Conta comum não pode acessar os endpoints
administrativos. **Esta migração precisa ser aplicada em produção por um
operador com credencial de migração antes de habilitar o cadastro.**
O serviço API Railway atual usa um pre-deploy de conectividade com o bridge;
ele não aplica migrations automaticamente. Evite habilitar SMTP/cadastro
sem confirmar `alembic upgrade head` aplicado ao banco de produção.

## Testes e rollout

`tests/integration/test_account_lifecycle.py` cobre registro, bloqueio
antes da verificação, e-mail sintético, token de uso único, login como membro
sem acesso ao QR, promoção com duas provas, expiração da sessão, reset e
invalidação de credenciais anteriores. O sistema legado continua coberto
pelos testes existentes.

Não executar envios reais ou distribuir links de redefinição em canais
de teste. Revisar rate limit e monitoração de entregabilidade SMTP antes
de ativar inscrições públicas. Ativação de cadastro e recuperação deve
ocorrer somente após teste completo de um endereço controlado e auditoria.
