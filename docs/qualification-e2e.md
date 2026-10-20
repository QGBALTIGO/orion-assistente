# Homologação real do onboarding e isolamento de empresas

Esta suíte acompanha uma pessoa desde o navegador até a API HTTPS real, o
PostgreSQL 16 migrado e a resposta renderizada. Não substitui rotas, dependências
FastAPI, autenticação, Redis, serviços de domínio ou PostgreSQL por doubles.
Contas, empresas e convites são sintéticos. Nenhum provedor é usado.

## Execução separada e obrigatória no CI

O arquivo `tests/e2e/qualification_e2e.py` tem exatamente **20 testes** e é
selecionado explicitamente. Ele não entra na coleta padrão dos testes unitários.
Não há skip quando falta PostgreSQL, Redis, Chromium ou ativação explícita: o job
falha. Não executar junto de outro processo que use o mesmo banco ou Redis /14.

Dependências do runner Ubuntu, além de `pip install '.[test]'`:

```sh
python -m pip install -r tests/e2e/requirements.txt
python -m playwright install --with-deps chromium
```

É necessário `openssl` no PATH. Não instalar Playwright na imagem de produção.
O fixture inicia e encerra seu próprio `uvicorn app.main:app`, com certificado
HTTPS autoassinado temporário, porta loopback efêmera e cookie real
`__Host-orion_session`. O Chromium ignora somente a confiança do certificado
local; autenticação, SameSite, CSRF e o atributo Secure continuam reais.

Use PostgreSQL **exclusivo, descartável**, com nome terminado em `_e2e`, três
papéis separados, e Redis 7 local na base 14. O fixture recusa URLs de bancos ou
Redis fora do loopback, papéis incorretos e banco com outro nome. Credenciais
abaixo pertencem apenas ao serviço efêmero do exemplo de CI:

```yaml
env:
  ORION_E2E_ENABLED: '1'
  APP_DATABASE_URL: postgresql+asyncpg://app_runtime:ci-runtime-only@127.0.0.1:5432/orion_e2e
  MIGRATION_DATABASE_URL: postgresql+psycopg://app_migrator:ci-migrator-only@127.0.0.1:5432/orion_e2e
  SUPPORT_DATABASE_URL: postgresql+asyncpg://app_support:ci-support-only@127.0.0.1:5432/orion_e2e
  ORION_E2E_REDIS_URL: redis://127.0.0.1:6379/14
  ORION_E2E_ARTIFACTS: test-results/e2e
```

Num job novo com PostgreSQL inicializado em `orion_e2e`, criar os papéis pelo
procedimento existente e aplicar o **head do próprio checkout**, sem baixar
schema de outra versão:

```sh
PGPASSWORD=ci-postgres-only psql -h 127.0.0.1 -U postgres -d orion_e2e \
  -v ON_ERROR_STOP=1 -v db_name=orion_e2e \
  -v runtime_password=ci-runtime-only -v migrator_password=ci-migrator-only \
  -v support_password=ci-support-only -f infra/postgres/init/001-roles.sql
alembic upgrade head
python -m pytest tests/e2e/qualification_e2e.py -vv -ra --maxfail=1 \
  --junitxml=test-results/e2e/junit.xml
```

Usar serviço PostgreSQL separado do job atual evita colisão dos papéis globais.
Não rodar `001-roles.sql` novamente num cluster que já contém esses papéis.
Se o runner reutilizar um cluster sintético, preparar o novo banco e permissões
deliberadamente, sem recriar os papéis ou tocar os bancos das demais suítes.
O job deve exigir sucesso e **20 pass / 0 skip**; a presença de coleta ou JUnit
sem execução não comprova homologação. O `--maxfail=1` para na primeira fronteira
quebrada, deixando screenshot, trace, eventos HTTP e log para diagnóstico.

## Provedores e dados

O subprocesso recebe uma lista mínima de variáveis. Não herda chaves, URLs ou
credenciais de provedores presentes no ambiente do runner. Liga apenas o
onboarding, perfis e catálogo necessários; WhatsApp gerenciado, marketing,
Google live sync, Groq/probe, pedidos e fulfillment continuam desligados.
O navegador bloqueia e registra qualquer requisição fora da origem loopback.
Não é iniciado worker nem bridge. Não há envio de e-mail, mensagens, pagamento,
publicação de site, geração de IA ou criação de dados reais.

As fixtures SQL só preparam contas com senha sintética Argon2, vínculos, legados
e convites necessários aos cenários. As ações sob teste passam pela interface e
pela API real. As verificações finais leem o banco com o papel de migração;
o servidor usa exclusivamente runtime/support como em produção. Os casos de
permissão também fazem uma tentativa adversa pelo próprio navegador autenticado,
com sessão e CSRF reais, para comprovar 403 sem alteração persistida.

Não há limpeza destrutiva por tabela no fixture. Os identificadores são únicos
e os registros permanecem disponíveis para diagnóstico até o serviço efêmero
ser descartado pelo CI. O certificado e a chave local vivem num diretório
temporário fora dos artefatos e são removidos após encerrar o servidor.

## Matriz de 20 jornadas distintas

| Caso | Fluxo e prova persistida |
|---|---|
| 01 | Cadastro no navegador, login real, entrada sem criar empresa, criação explícita de uma empresa pausada e um proprietário |
| 02 | Serviços: endereço privado, profissional, preço/duração/buffers, vínculos, jornadas e pausas, perfil em rascunho e conclusão explícita |
| 03 | Produtos: variante/preço reais, ausência de agenda, estoque desconhecido sem quantidade inventada e conclusão |
| 04 | Negócio misto: serviço e produto persistidos no mesmo escopo, sem confundir operações ou ativar canais |
| 05 | Reload e edição cancelada preservam dados gravados e preço original sem duplicação |
| 06 | Duas empresas exigem escolha; a configuração consultada pertence somente à escolhida |
| 07 | Novo negócio explícito usa formulário vazio, cria segundo escopo e preserva o catálogo anterior |
| 08 | Proprietário legado com estrutura válida entra no painel sem onboarding retroativo nem nova empresa |
| 09 | Legado vazio usa a mesma empresa ao salvar a configuração opcional |
| 10 | Editor entra pelo acesso real e recebe 403 ao tentar assumir a configuração de proprietário |
| 11 | Leitor consulta e recebe 403 ao tentar concluir/ativar cadastros |
| 12 | Convite pendente prende a entrada no acompanhamento sem criar proprietário ou empresa |
| 13 | Aceite real persiste `claimed` sem vínculo; outro navegador do proprietário revisa e aprova, só então cria vínculo de editor |
| 14 | Logout remove sessão real e o reload não mostra dados autenticados nem cria empresa |
| 15 | Duas abas reais disputam CAS: a segunda recebe 409, mantém texto digitado e não sobrescreve a primeira |
| 16 | Duplo submit mantém uma criação, um proprietário e uma identidade idempotente |
| 17 | Resposta perdida depois do commit real é recuperada pela leitura do ledger sem segundo POST |
| 18 | Conta de outra empresa não lê nem altera o escopo estrangeiro, mesmo com URL/ID conhecido |
| 19 | Revogação do proprietário após renderizar invalida o salvamento e preserva zero escrita da etapa |
| 20 | Empresa concluída abre Canais WhatsApp; flag desligada devolve 423 e nenhum provedor é iniciado |

O caso 17 usa `route.fetch()` para deixar o pedido chegar e gravar no backend
real, e descarta **apenas a resposta de transporte**. Não fornece resposta falsa
nem substitui o domínio. A recuperação é uma ação explícita de consulta.

## Evidências e limites

`test-results/e2e/api.log` registra o servidor real sem access log de payloads.
Cada teste grava `http.json` com método, caminho e status, erros JavaScript e
tentativas externas bloqueadas. Em falha, grava `failure.png` com campos de senha
mascarados e `trace.zip` sanitizado. O trace bruto fica em um diretório temporário
fora dos artefatos e é apagado: headers/cookies/payloads de login, valores
preenchidos, senha sintética e tokens opacos não seguem no ZIP publicado. O teste
unitário `tests/e2e/test_artifact_sanitization.py` verifica essa remoção sem serviços.
O relatório de falha também redige a senha sintética e tokens opacos.
Upload sugerido do diretório inteiro como artefato do job, inclusive em falha.
O relatório JUnit distingue execução de coleta/skips.

Esta preparação não afirma que os 20 testes já passaram. A execução real no CI
é obrigatória antes de marcar a homologação concluída. Mesmo depois de verdes,
ela não prova pareamento WhatsApp, entrega/legibilidade no celular, OAuth real,
pagamento ou qualidade visual em todos os aparelhos.

Referências primárias da ferramenta: [Playwright Python](https://playwright.dev/python/docs/intro)
e [API de páginas](https://playwright.dev/python/docs/api/class-page).
