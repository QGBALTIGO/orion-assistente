# Solicitações comerciais da landing Orion

## O que este fluxo faz

A landing recebe uma solicitação de teste de três dias, interesse em plano ou
contato. O registro fica no PostgreSQL para a equipe Orion consultar em
**Configurações → Solicitações do site**. O visitante recebe confirmação somente
depois do commit. Este fluxo não cria conta, empresa, membership, assinatura,
prazo de teste, cobrança ou mensagem externa.

Os três dias são combinados na ativação assistida. O envio do formulário não
inicia um contador e não modifica assinaturas existentes. Os nomes comerciais
Essencial, Profissional e Escala são intenções de contato; não constituem novos
registros em `plans` nem definem preço, limite ou direito de uso.

## Contrato público

`POST /api/public/sales-requests`

Headers: `Content-Type: application/json`, `Idempotency-Key: <UUIDv4>` e `Origin`
da página. O navegador fornece Origin. Não é necessário expor token operacional
ou criar uma sessão de administrador para enviar o formulário.

```json
{
  "name": "Pessoa de exemplo",
  "email": "pessoa@example.invalid",
  "phone": "",
  "business_name": "",
  "segment": "outro",
  "intent": "trial",
  "consent": true,
  "website": ""
}
```

Campos:

| Campo | Contrato |
|---|---|
| `name` | Obrigatório; 2–120 caracteres após normalização. |
| `email` | Obrigatório; endereço normalizado para minúsculas, até 254 caracteres. A validação sintática não prova posse do endereço. |
| `phone` | Opcional; aceita máscara brasileira e persiste `+55...`. Não verifica se o número tem uma conta WhatsApp. |
| `business_name` | Opcional; até 160 caracteres. Vazio vira `null`. |
| `segment` | `barbearia`, `estetica`, `odontologia`, `restaurante`, `fitness` ou `outro`. |
| `intent` | `trial`, `essencial`, `profissional`, `escala` ou `contact`. |
| `consent` | Booleano `true` obrigatório; autoriza contato para atender esta solicitação. |
| `website` | Campo antiautomação; deve ficar vazio. Nunca é persistido. |

O servidor persiste a versão de consentimento `sales-contact-v1` e a data do
aceite. O texto correspondente é: “Autorizo o contato da equipe Orion para
responder à minha solicitação.” Isso não representa opt-in para campanhas em
massa, confirmação de contrato ou autorização para criar uma assinatura.

A chave deve ser nova para cada solicitação intencional. Em caso de erro de
transporte, reutilizar a mesma chave e o mesmo payload normalizado permite
recuperar o resultado sem duplicação. Reutilizar uma chave com dados diferentes
retorna `409`. O cliente mantém a chave somente em memória e não deve gravar
contatos em armazenamento local do navegador.

Sucesso e replay equivalente retornam `201`:

```json
{"ok": true, "request_id": "UUID da solicitação"}
```

Essa resposta não contém nome, e-mail, telefone ou informações de infraestrutura.
Não existe GET público para recuperar os dados pela identificação do recibo.

### Falhas e limites

- `403`: origem ausente, diferente ou Fetch Metadata incompatível.
- `408`: envio do corpo não terminou dentro do prazo.
- `409`: chave idempotente reutilizada com outra solicitação.
- `413`: corpo maior que 8 KiB, inclusive em streaming sem Content-Length.
- `415`: conteúdo diferente de JSON.
- `422`: dados, consentimento, identificador ou campo antiautomação inválidos.
- `429`: limite atingido; `Retry-After` informa quando tentar novamente.
- `503`: configuração, Redis ou persistência indisponível. Não apresentar sucesso.

O limitador é atômico no Redis: 20 tentativas por endereço de conexão em dez
minutos e cinco por e-mail em uma hora. As duas condições precisam permitir o
envio. Chaves de limite usam HMAC com o token operacional já configurado; não
contêm o e-mail ou IP em texto. `X-Forwarded-For` fornecido diretamente pelo
cliente não é usado pelo módulo; a identidade é a que o servidor ASGI informa.
Esses limites são proteções operacionais, não franquias de planos.

O deadline do Redis é de dois segundos. A operação de banco tem deadline de
cinco segundos e statement timeout de 2,5 segundos. Erros são apresentados sem
URL de conexão, segredo ou payload de contato. O formulário deve conservar os
dados em memória em caso de falha para permitir uma nova tentativa.

## Acesso da equipe

Somente os papéis globais `support` e `admin` podem acessar:

- `GET /api/v1/admin/sales-requests`
- `PATCH /api/v1/admin/sales-requests/{id}`

Ser owner/editor/viewer de uma empresa não concede acesso a solicitações
comerciais da plataforma. A seção pode funcionar sem uma empresa selecionada.
Sessões web usam a proteção CSRF existente do painel nas mutações.

GET aceita `status=all|new|contacted|closed`,
`intent=all|trial|essencial|profissional|escala|contact`, `q` até 120 caracteres,
`limit` de 1 a 100 e `offset` de 0 a 100.000. A ordenação é por data e UUID,
decrescente. `total` aplica os filtros; `counts` contém os totais globais por
status, explicitamente separados da paginação. `access.can_update=true` só é
devolvido depois da autorização global.

PATCH aceita somente `status` e `expected_version`. Os estados exibidos são
Nova, Contatada e Encerrada. Uma alteração real incrementa a versão e registra
papel/conta responsáveis; edições concorrentes antigas recebem `409`. Marcar
Contatada registra a primeira data desse estado, mas não envia mensagem nem
comprova que um destinatário leu um contato. Uma atualização sem mudança de
estado conserva a versão. Não há exclusão ou edição dos dados originais pelo
endpoint de acompanhamento.

## Persistência e implantação

A revisão nova é **`0018_sales_requests`**, após
`0017_financial_settlements`. Ela cria somente a tabela `sales_requests` e seus
índices/políticas. A tabela tem RLS e FORCE RLS. `app_runtime` e PUBLIC não têm
acesso; `app_support` pode inserir/consultar e atualizar somente colunas de
acompanhamento. Os campos originais, a chave, o hash e o aceite não podem ser
reescritos com o privilégio de UPDATE da aplicação. O proprietário esperado é
`app_migrator`. Um downgrade é recusado se houver solicitações armazenadas.

Nenhuma migração é executada pelo startup normal. A presença do novo código não
substitui a aplicação da revisão: sem a tabela, a captação retorna `503`.

### Operação restrita para a revisão 0018

O runner **`scripts/one_time_sales_requests_migration.py`** executa exclusivamente
**0017 → 0018**. Ele não usa `alembic upgrade head`, não altera runners anteriores
e não cria um mecanismo novo de backup ou transporte de dados de produção. O
ensaio com `pg_dump`/`pg_restore`, dados sintéticos, owners e ACLs faz parte do
gate PostgreSQL descartável descrito abaixo.

A execução exige as variáveis existentes `MIGRATION_DATABASE_URL`,
`ORION_EXPECTED_DATABASE_HOST` e `ORION_EXPECTED_DATABASE_NAME`. Host e nome do
banco precisam coincidir exatamente com a URL. O driver aceito é PostgreSQL com
psycopg; somente as opções de transporte `sslmode` e `sslrootcert` são aceitas.
O servidor precisa ser PostgreSQL 16. Os três papéis da aplicação devem existir
sem SUPERUSER ou BYPASSRLS.

Quando a URL usa o papel proprietário operacional, manter
`ORION_MIGRATION_SWITCH_ROLE=1`: o DDL executa com `SET LOCAL ROLE app_migrator`.
Uma conexão já autenticada como `app_migrator` pode omitir a troca. Nenhum outro
papel pode executar a operação. O processo normal `scripts.start_api` remove a
URL, as identidades esperadas e as flags de execução única antes de iniciar a API.

Com o commit aprovado e as variáveis privadas já configuradas no serviço:

```sh
ORION_RUN_SALES_REQUESTS_MIGRATION=1 python -m scripts.one_time_sales_requests_migration
```

A flag ausente ou igual a `0` produz `{"ok": true, "status": "disabled"}` sem
abrir uma conexão. Outros valores são recusados. Configurar o comando temporário
de pre-deploy em sequência com a verificação QR existente, preservando
`python -m scripts.probe_qr_bridge`. O operador retira a flag e o comando
temporários após comprovar a revisão; o startup regular continua sem executar
migrations.

A operação aplica estas verificações:

1. Confere os bytes SHA256 da migration antes de executar seu Python ou abrir
   o banco. Somente CRLF de checkout é normalizado para LF.
2. Valida papel, banco, PostgreSQL 16 e ausência de event triggers. Usa advisory
   lock exclusivo da operação; uma execução simultânea recebe
   `migration_already_running`, sem retry automático. O lock timeout é de cinco
   segundos; o statement timeout é de 30 segundos.
3. Exige uma única revisão `0017_financial_settlements`, exatamente as 66 tabelas
   do baseline selado e ausência de `sales_requests`. Relações públicas fora de
   tabelas e índices são recusadas. A tabela `alembic_version` precisa manter a
   estrutura mínima aprovada, sem regras, triggers, CHECKs, FKs ou índices extras
   que poderiam causar escritas indiretas no momento de atualizar a versão.
4. Executa a única migration e o UPDATE de `alembic_version` na mesma transação
   `REPEATABLE READ`, com bloqueio da linha de versão. Captura antes e compara
   depois o catálogo das tabelas existentes, owner, ACLs, RLS, políticas,
   colunas, constraints, índices, regras, triggers de usuário, funções e grants
   padrão. A comparação final ocorre depois do UPDATE de versão.
5. Confere a estrutura integral aprovada da tabela nova e confirma que ela
   está vazia na aplicação inicial. Qualquer falha desfaz DDL e revisão. Não há
   reparo, downgrade, seed ou escrita em tabelas operacionais existentes.
6. Em uma revisão já `0018_sales_requests`, valida a estrutura em uma transação
   explicitamente **READ ONLY** e retorna `already_applied`. Não consulta nem
   reescreve os contatos recebidos. Qualquer outra revisão, tabela adicional ou
   estrutura incompatível é recusada.

O runner não varre ou calcula hashes das linhas de negócio em produção. Os
hashes de conteúdo ficam apenas nos testes sintéticos. Os triggers internos de
integridade referencial que PostgreSQL cria para a nova FK são a única adição
indireta permitida; triggers de usuário existentes são comparados exatamente.

Saída de sucesso: `ok`, `status` (`applied` ou `already_applied`), `source` e
`revision`. Falha retorna exit code 1 e um código de fase fixo, sem traceback,
URL de banco, senha ou dados de contato. Este procedimento documenta uma ação
operacional explícita; não autoriza nem executa produção por si só.

### Manifesto revisado em PostgreSQL 16

Os hashes abaixo são literais no runner. O manifesto é JSON canônico, com chaves
ordenadas e separadores sem espaço. Ele é obtido de `pg_catalog`, usando
`search_path=pg_catalog,public`, e inclui definições e metadados de permissões.

| Identidade | SHA256 |
|---|---|
| Arquivo `0018_sales_requests.py` com LF | `e253082452d91fd31490f95d1b3dd0c88cffa155aaca48c1a06b980004cc9a3f` |
| Catálogo de `sales_requests` | `2d9de1cad02f6d38d6cde3cdaa83d8f36ccc9d151d3ca743c7ffb6c8a6fa2859` |
| Estrutura mínima de `alembic_version` | `5e9c39355414d66eb9c65a30febb743df9fa210246ac482ca2f1e16b831a26f4` |

`sales_requests` é uma tabela permanente, owner `app_migrator`, com RLS e FORCE
RLS. Tem 19 colunas, 17 constraints, cinco índices B-tree e duas políticas
permissivas `FOR ALL`: `sales_requests_support` e `sales_requests_migrator`,
ambas com `USING(true)` e `WITH CHECK(true)` para o papel nominal correspondente.
Não tem regras ou triggers de usuário.

| Colunas | Tipo e condição |
|---|---|
| `id` | UUID, PK, obrigatório, default `gen_random_uuid()` |
| `idempotency_key` | UUID obrigatório e único; versão UUIDv4 conferida por CHECK |
| `payload_hash` | `char(64)` obrigatório; hexadecimal minúsculo |
| `name`, `email` | `varchar(120)` e `varchar(254)`, obrigatórios; tamanho e normalização conferidos |
| `phone`, `business_name` | `varchar(16)` e `varchar(160)`, opcionais; formato/tamanho conferidos |
| `segment`, `intent` | `varchar(20)` obrigatórios; enums conforme contrato público |
| `consent`, `consent_version` | Booleano obrigatório `true` e `varchar(32)` igual a `sales-contact-v1` |
| `consented_at`, `created_at`, `updated_at` | `timestamptz` obrigatórios, default `now()` |
| `status`, `version` | `varchar(12)` obrigatório, default `new`; integer obrigatório ≥1, default 1 |
| `contacted_at` | `timestamptz` opcional; obrigatório quando o estado é `contacted` |
| `updated_by_role`, `updated_by_account_id` | Papel `support`/`admin` opcional e UUID opcional; FK para `panel_accounts(id)` com `ON DELETE SET NULL` |

Os cinco índices são a PK, a unicidade de `idempotency_key` e
`ix_sales_requests_created`, `ix_sales_requests_status_created`,
`ix_sales_requests_intent_created`. Os três últimos usam data/UUID descendentes
e os campos de filtro correspondentes. O manifesto também confere validade,
prontidão, unicidade e owner de cada índice.

PUBLIC e `app_runtime` não recebem privilégios. `app_support` recebe SELECT e
INSERT na tabela; UPDATE somente em `status`, `version`, `updated_at`,
`contacted_at`, `updated_by_role` e `updated_by_account_id`. Nenhum campo original
de contato, consentimento ou idempotência recebe UPDATE. O manifesto confere
também os privilégios efetivos dos papéis, além das ACLs explícitas.

`alembic_version` permanece uma tabela sem RLS, owner `app_migrator`, com uma
única coluna `version_num varchar(32) NOT NULL`, PK/índice
`alembic_version_pkc` e nenhuma permissão para runtime ou suporte. Nenhum
default, regra, trigger, FK, CHECK ou índice extra é aceito nessa tabela.

### Configuração a conferir

- `PANEL_PUBLIC_URL`: quando configurado, precisa ser a origem HTTPS exata que
  hospeda a landing. Um domínio alternativo retorna `403` intencionalmente.
  Sem essa variável, a origem é comparada com a origem do request ASGI.
- `SUPPORT_API_TOKEN`: segredo existente, mínimo de32 caracteres, também usado
  para derivar chaves opacas de rate limit.
- `SUPPORT_DATABASE_URL`: conexão existente com papel `app_support`, no banco
  em que a migration foi aplicada.
- `REDIS_URL`: infraestrutura já usada pelo Orion. Indisponibilidade impede
  captação; não há bypass silencioso do limite.
- `SALES_WHATSAPP_E164`: opcional. A captação principal funciona sem número
  comercial; somente um link alternativo dependeria dessa configuração.

## Assets da landing

`/landing-assets/{basename}` serve apenas a allowlist definida em
`app/landing_api.py`: CSS, JS, fotografias WebP, vídeo MP4, poster e imagem social.
Os arquivos permanecem em `landing/assets/`, já incluído pelo Dockerfile.
MIME é explícito, `nosniff` é aplicado e arquivos estáveis usam `no-cache`.
FileResponse suporta Range para o vídeo. Manifesto de fontes, Markdown,
arquivos Python e caminhos arbitrários não são expostos pela rota.

## Verificação

Os testes específicos ficam em:

- `tests/unit/test_sales_requests_unit.py`
- `tests/integration/test_sales_requests.py`
- `tests/unit/test_landing_api_unit.py`
- `tests/unit/test_one_time_sales_requests_migration_unit.py`
- `tests/integration/test_one_time_sales_requests_migration.py`
- `tests/unit/test_start_api.py` (remoção das credenciais e da nova flag)
- `tests/integration/test_billing.py` (contrato HTTP da landing)
- `ui-tests/site-requests.test.mjs` (consulta e acompanhamento pela equipe)

Os gates PostgreSQL cobrem persistência do aceite, ausência de criação de conta
ou assinatura, concorrência e replay, filtros/total/paginação, controle de
versão, RLS/grants e rate limit com Redis real. Usar bancos descartáveis e dados
sintéticos. Não enviar solicitações fictícias para produção apenas para testar
o formulário e não disparar mensagens externas durante esses testes.

O gate do runner também cria bancos UUID locais próprios, restaura um dump
0017 com owners e ACLs, confere hashes de todas as tabelas antigas exceto a linha
de versão e aplica a migration tanto no ensaio restaurado quanto na fonte
sintética. Cobre replay READ ONLY com uma solicitação já recebida, adulterações
de owner/grants/RLS/colunas/constraints/índices/políticas, fonte errada, revisão
duplicada, rollback após escrever a versão, alteração indevida do catálogo,
triggers/regras/CHECKs que tentariam alterar um canário, papéis efetivos e lock
concorrente. Nenhum teste desse fluxo consulta produção.

O teste dedicado exige `ORION_MIGRATION_TEST_OWNER_URL` apontando para o banco
`postgres` em loopback, com usuário `postgres`, clientes PostgreSQL 16 e papéis
da aplicação já criados pela configuração de testes. Definir
`ORION_REQUIRE_MIGRATION_OPS_TEST=1` torna essa infraestrutura obrigatória em vez
de permitir skip. Com as variáveis sintéticas de teste já configuradas:

```sh
python -m pytest -q -ra \
  tests/unit/test_one_time_sales_requests_migration_unit.py \
  tests/unit/test_start_api.py \
  tests/integration/test_one_time_sales_requests_migration.py
```

A regressão dirigida desta implementação passou em **56 casos**, sem skips,
incluindo PostgreSQL 16 e backup/restore real. A rodada adjacente de captação,
landing, autenticação, billing e lifecycle passou em **87 casos**, sem skips.
A regressão integral deve ser conferida no commit exato do PR antes de merge.
Ver também [redesign da landing](landing-redesign.md) e
[operação da suíte de gestão](management-suite-runbook.md).
