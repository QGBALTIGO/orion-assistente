# Recibo final de qualificação do código

Código qualificado: `b6650a0f82cc03b8c9238406dbe6ec2567a32778`.
Branch: `codex/orion-marcflow-integrations-20261020`.
Estado da missão completa: **PARCIAL**.

| Verificação | Resultado | Evidência |
|---|---|---|
| PostgreSQL/Redis reais e descartáveis | 4.149 passaram, 1 ignorado, 0 falhas | [CI 39440009444](https://github.com/QGBALTIGO/orion-assistente/actions/runs/39440009444) |
| Docker Compose | 4.038 passaram, 112 ignorados, 0 falhas | Mesma CI; conjunto de variáveis diferente |
| Conector Linux | 413 passaram, 0 falhas, 0 ignorados | Mesmo trabalho de CI |
| Interface DOM | 298 passaram, 0 falhas, 0 ignorados | Mesmo trabalho de CI |
| Uploads/mídia | Trabalho concluído com sucesso | [CI 39440009620](https://github.com/QGBALTIGO/orion-assistente/actions/runs/39440009620) |
| ASR sintético, build Baileys, backup/restore e saúde HTTP | Trabalhos concluídos com sucesso | CI principal |
| Chromium sintético | Seis cenários, 360/768/1440 px, claro/escuro; sem overflow ou envio | `ui-tests/inbox-browser.mjs` |
| Migrations | Upgrade, downgrade vazio, reaplicação e recusa de perda de dados | `tests/integration/test_inbox_migration_postgres.py`, CI principal |

Os conjuntos possuem casos sobrepostos. As contagens não devem ser somadas como
quantidade de testes únicos. Os testes opcionais de proprietário do banco
descartável foram executados na primeira suíte; não estão configurados no
contêiner de teste do Compose.

O registro anterior em `validation.json` corresponde ao snapshot `84e32bc` e foi
preservado. As quatro falhas locais por URLs de banco ausentes foram reproduzidas
na base original; não ocorreram na suíte PostgreSQL/Redis qualificada acima.

Uma rodada intermediária falhou no teste novo de reserva porque a fixture
sintética não habilitava `QR_CHANNELS_ENABLED`. A correção foi restrita à fixture,
mantendo a autorização e as asserções de concorrência. A qualificação final cobre
também retomada após pausa do Redis, revalidação concorrente sob locks de leitura,
resposta atrasada de gravação após troca de empresa e desativação da assistente
entre a leitura das credenciais e a reserva do envio.

As novas flags continuam desligadas por padrão. Não houve deploy, migration em
banco operacional, alteração de sessão ou mensagem enviada a contato real.
Navegador com dados sintéticos e CI descartável não equivalem a homologação de
telefone físico ou WhatsApp real.

A tentativa de varredura automatizada de segurança não iniciou os workers por
ausência de perfil gerenciado de leitura. O erro e a limitação estão registrados
na seção 9 do relatório de integração. Estes resultados funcionais não constituem
uma auditoria de segurança completa nem concluem a missão MarcFlow → Orion.
