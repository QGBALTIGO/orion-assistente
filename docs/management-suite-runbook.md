# Suíte de gestão Orion: instalação, validação e implantação

Este documento acompanha a [referência visual e técnica](barberpilot-orion.md)
e a [matriz de cobertura funcional](barberpilot-functional-coverage.md).
As alterações partem de `3f171babc4e7e5d0736442403bfdb49dab513264` (PR #52).
Os comandos abaixo são instruções de operação: sua presença no repositório
não significa que tenham sido executados na Railway ou em uma conta real.

## Estado verificado em 18 de outubro de 2026

| Verificação | Resultado local |
|---|---|
| Instalação em banco vazio, Alembic 0001 → 0017 | Aprovada em PostgreSQL 16.15 |
| Regressão Python completa, com PostgreSQL e Redis reais | **2.096 passaram; 1 ignorado; 0 falhas** em 54,32 s |
| Teste ignorado | Caso exclusivo de Windows, executando a suíte em Linux |
| Suíte existente Node do conector e painel | **304 passaram; 0 falhas** |
| Testes DOM da nova suíte e do site público | **25 passaram; 0 falhas**, com HTML/JavaScript reais e APIs sintéticas |
| Arquivos estáticos por HTTP/ASGI | Rotas, MIME, arquivos permitidos, fontes e páginas de entrada aprovados |
| Pacote wheel | Build aprovado; inclui JS/CSS/HTML da suíte, site e convite, fontes e licenças |
| Docker, imagem Baileys e ASR real | Gates mantidos no GitHub Actions; não executados no Docker local desta sessão |
| Comparação visual da branch no navegador | Pendente de implantação HTTP(S) desta versão |
| Mensagens, pagamentos e IA de provedor com credenciais reais | Não acionados nesta validação |

A execução completa usou um banco novo com papéis `app_runtime`,
`app_migrator` e `app_support`, sem BYPASSRLS; Redis 7.0.15 em loopback;
Python 3.12 e Node 24.19.0. Os testes Node do CI continuam usando Node 22.
Houve avisos existentes de depreciação de eventos FastAPI, TestClient/HTTPX
e configuração Alembic; não foram mascarados.

Antes do resultado final, o ambiente descartável precisou de duas correções:
o limite de 30 conexões foi ajustado para 100 para a prova de cinquenta
reservas concorrentes; os clientes PostgreSQL 16 receberam as bibliotecas
locais e uma URL administrativa de teste completa. A execução posterior
passou integralmente. Essas correções não alteraram a configuração publicada.

## Migrations

A atualização de um banco na revisão `0012_commercial_fulfillment` adiciona
cinco migrations, na seguinte ordem:

| Revisão | Mudança principal |
|---|---|
| `0013_management` | Clientes com campos ampliados e telefone opcional; serviços e profissionais; gestão, relatórios, metas e conclusão/falta de atendimentos |
| `0014_public_catalog` | Combos, adicionais, variantes internas, snapshot dos componentes, publicação e métricas públicas |
| `0015_loyalty_rewards` | Pontos por conclusão, cupons, resgate, desconto e snapshots de comissão |
| `0016_management_extras` | Bloqueios recorrentes, uso da IA, preferências, leitura de notificações e convites revisados |
| `0017_financial_settlements` | Quitações imutáveis e compensação parcial dos vales |

`app_runtime` atende os dados da empresa sob RLS. A fronteira de contas,
convites e memberships usa `app_support` conforme o modelo já existente.
Somente `app_migrator` aplica DDL. A aplicação e o worker não devem receber
credenciais administrativas para iniciar normalmente.

As migrations devem ser aplicadas **antes** de colocar a nova API e o worker
em execução: consultas do motor passam a usar, por exemplo,
`professionals.accepts_appointments` e os snapshots de descontos/comissões.
Não há preenchimento de empresas, clientes, vendas ou agendamentos de demonstração.

### Desenvolvimento local

Com o ambiente e papéis do [README](../README.md) configurados:

```sh
docker compose up -d --wait postgres redis
docker compose --profile tools run --rm --build migrate
docker compose --profile tools run --rm --build test
docker compose up -d --build api worker
```

O `migrate` executa Alembic com a credencial de migration; a suíte Docker
exige PostgreSQL e Redis reais. Sem Docker, use Python 3.12, as dependências
`.[test]` e as URLs dos três papéis em um banco de testes previamente migrado:

```sh
alembic upgrade head
pytest -q -ra
node --test qr-bridge/test/*.test.mjs
npm ci --prefix ui-tests --ignore-scripts --no-audit --no-fund
npm test --prefix ui-tests
```

As URLs e senhas devem vir do ambiente, não de argumentos públicos ou arquivos
versionados. `REQUIRE_POSTGRES_TESTS=1` e `REQUIRE_REDIS_TESTS=1` transformam
ausência desses serviços em falha explícita. O teste de intake existente usa
`ORION_INTAKE_TEST_REDIS_URL` e `ORION_REQUIRE_INTAKE_REDIS_TEST=1`.

Os testes de backup/restauração existentes exigem cliente PostgreSQL 16 e
`ORION_MIGRATION_TEST_OWNER_URL` para uma instância administrativa **local e
descartável**, além de `ORION_REQUIRE_MIGRATION_OPS_TEST=1`. Eles criam e removem
seus próprios bancos sintéticos; não apontar esse teste para um banco operacional.

## Configuração da nova gestão

As variáveis adicionadas estão em [`.env.example`](../.env.example).

| Variável | Uso e comportamento |
|---|---|
| `MANAGEMENT_AI_ENABLED` | `0` mantém as gerações indisponíveis. `1` habilita a capacidade se a credencial e o modelo forem válidos. Sem definição explícita, o código respeita o fallback de `GROQ_PREVIEW_ENABLED`. |
| `GROQ_API_KEY` | Credencial de servidor para a IA; nunca enviada ao frontend. |
| `GROQ_MANAGEMENT_MODEL` | Modelo permitido na configuração Groq do Orion. O exemplo usa `openai/gpt-oss-120b`. |
| `MANAGEMENT_AI_DAILY_LIMIT` | Limite diário por empresa, padrão 50, configurável entre 1 e 500. A contagem reinicia pelo dia UTC. |
| `PUBLIC_SITE_SIGNING_KEY` | Chave dedicada de pelo menos 32 caracteres para os resumos de agendamento público. Se omitida, usa derivação específica do `SUPPORT_API_TOKEN` válido. |
| `PANEL_PUBLIC_URL` | Origem HTTPS dos links de convite. Quando omitida, o link é relativo ao painel; uma origem preenchida inválida é recusada. |
| `REDIS_URL` | Conexão de servidor; necessária para publicação e limites atômicos do site público. |
| `BUSINESS_PROFILES_ENABLED` | Capacidade já existente que deve estar habilitada para o acesso das contas por membership. |

Não habilitar IA por simplesmente implantar a interface. A tela informa
status/configuração, quota e consumo retornados pelo servidor. Não existe
equivalência de créditos pagos ou carteira do BarberPilot: o histórico registra
tokens efetivamente informados pelo provedor e tentativas dentro da quota.

### Site público

1. Cadastre serviços ativos, profissionais aptos e expediente.
2. Salve nome público, slug e preferências em **Meu site**.
3. Use a prévia autenticada para revisar as informações; ela não permite reserva.
4. O proprietário publica a versão salva por uma ação explícita.
5. A página fica disponível em `/s/{slug}` e o agendamento usa o motor do Orion.

O snapshot público contém preferências como título, descrição, imagens,
contatos e endereço. O catálogo de serviços, vínculos, preços e horários
continua operacional e é revalidado. Editar o rascunho de preferências não
publica automaticamente essa edição. Slugs conflitantes retornam erro.

A disponibilidade produz resumo assinado com validade de dez minutos. A
confirmação exige `confirmed: true`, dados válidos e um horário ainda
disponível. O servidor recalcula o contrato da seleção; o cliente não fornece
o preço final. Repetir uma confirmação cujo retorno se perdeu recupera o
agendamento correspondente, sem multiplicar reservas nem métricas.

Imagens são URLs HTTPS. Upload, corte de fotos, domínio próprio, carrossel,
favoritos e avaliações públicas não integram esta versão. A matriz de cobertura
detalha os demais limites. O mapa abre o endereço configurado; não cria
coordenadas ou localização fictícia.

### Equipe e convites

O proprietário escolhe `editor` ou `viewer` e gera um link. O token bruto é
exibido uma vez; somente seu hash permanece armazenado. Uma repetição idempotente
não recupera um token que já não esteja disponível.

A pessoa entra em sua própria conta e aceita o convite. Esse aceite registra
uma solicitação, que ainda exige aprovação do proprietário. Vínculo por e-mail,
validade, versão e membership atual são verificados no servidor. Aprovações
concorrentes não sobrescrevem acessos existentes. A criação do convite não
envia e-mail nem mensagem automaticamente.

### Automações, fila e notificações

As onze rotinas de marketing são **rascunhos persistidos**. Salvar uma opção
de automação não inicia o executor, não dispara mensagens, não pede avaliação
Google e não envia um resumo semanal. `execution_enabled` permanece falso e
as ações de campanha continuam sujeitas aos bloqueios existentes do Orion.

A fila exibida é a fila real de respostas Meta (`message_outbox`), identificada
como tal. Ela não deve ser interpretada como prova de campanhas enviadas.
Os estados pendente/enviado/falha e tentativas vêm dos registros persistidos.

As notificações internas derivam de eventos reais de agendamento, com leitura
individual por conta. Isso não equivale a push no navegador ou no aparelho.
Nenhuma nova configuração muda por si só `META_LIVE_SEND`, `QR_AI_ENABLED`
ou `GOOGLE_LIVE_SYNC`.

## Cenários de aceite cobertos

Os arquivos em [tests/integration](../tests/integration) e
[tests/unit](../tests/unit) documentam as condições de cada prova.

| Área | Prova relevante |
|---|---|
| Isolamento | Empresa, cliente, profissional, cupom, lançamento e convite de outro escopo são recusados; a consulta sem escopo não lê dados |
| Concorrência de agenda | Cinquenta solicitações concorrentes ao motor produzem um único hold no mesmo horário |
| Reserva pública | Seis clientes concorrentes em combo com adicional produzem uma confirmação e cinco conflitos |
| Redis | Vinte e cinco admissões concorrentes com limite sintético sete admitem somente sete e mantêm TTL |
| Idempotência | Reserva, conclusão, pontos, cupom, quitação e operações de gestão não duplicam a mesma intenção |
| Horários | Folgas, pausas, conflitos de série, remoção por escopo e transições de fuso/DST |
| Histórico | Preço/composição do combo, comissão, desconto e valores anteriores permanecem após mudanças do catálogo |
| Comissão | Vales parciais, saldo acumulado, snapshot desatualizado, concorrência e estorno não suportado recusado |
| IA | Contrato estruturado, ausência de ferramentas, limites, repetição, resposta inválida e revogação de acesso |
| Publicação | Somente owner; rascunho isolado; catálogo público sem dados privados; indisponibilidade segura sem infraestrutura |
| Equipe | Convite expirado, conta errada, revisão de versão, aceite sem acesso automático e aprovação concorrente |
| Painel | Arquivos disponíveis, MIME correto, HTTP 204, sessão, respostas atrasadas e comandos preservados |

Os testes DOM usam os arquivos reais com jsdom e respostas de API sintéticas.
São complementares aos testes PostgreSQL, e não um teste de navegador real.

## Implantação revisável na Railway

A leitura da configuração em 18/10/2026 encontrou API, worker e QR bridge
vinculados à branch `main`, com PostgreSQL e Redis existentes. Não havia
alterações de ambiente pendentes nessa leitura. Nenhum serviço, variável,
volume ou domínio foi alterado por este trabalho.

Antes de integrar e implantar:

1. Revise o PR e os resultados dos jobs Python/PostgreSQL, Node/Docker e ASR.
2. Confirme a revisão atual do banco e as credenciais de migration separadas.
3. Faça e confira backup/restauração pelo procedimento operacional existente.
4. Aplique `alembic upgrade head` com `app_migrator` em uma execução operacional
   explícita; confira `0017_financial_settlements` e as políticas RLS.
5. Publique a API e o worker do commit aprovado e confira `/health`.
6. Entre no `/panel` com uma conta de teste já autorizada e valide os fluxos
   de gestão, sem acionar clientes, pagamentos ou canais reais.
7. Compare o layout a 390 × 844, 820 × 1.180 e 1.440 × 1.000, nos temas usados,
   e depois em aparelhos físicos, incluindo foco, rolagem e teclado virtual.
8. Habilite capacidades externas separadamente, quando houver configuração
   e decisão explícita para o ambiente correspondente.

Criar a branch ou o PR não aplica migrations e não muda o painel publicado.
A descrição existente da Railway está em
[railway-homologacao.md](railway-homologacao.md); confirme o estado atual antes
de usar comandos históricos. O script de migration operacional dedicado a
0010/0012 não deve ser tratado como executor genérico dessas novas revisões.

### Retorno de versão

Não execute um downgrade automático em um banco já utilizado. Algumas revisões
recusam downgrade se houver combos, cupons, descontos ou quitações; a revisão
0016 possui remoção de suas tabelas no caminho de downgrade. Há dados novos que
o binário antigo não conhece, como clientes sem telefone e atendimentos concluídos.

Um retorno operacional exige parar as gravações afetadas, avaliar compatibilidade
do binário e tratar eventual restauração do backup como uma operação de dados
explicitamente revisada. Apagar volumes ou históricos não é uma etapa de
atualização da suíte.
