# Recibo da central humana e rascunhos de fluxo

Data: 20/10/2026. Código qualificado: `e896d2a31b464bf1ed7b7a1fe0789782bb9cf5b5`.
Branch: `codex/orion-marcflow-integrations-20261020`.
Continuação sobre `4b1ba803d7fc3539f85a81f3e5b5f4899653ff42`; PR #80 em rascunho.

Um commit posterior pode registrar este recibo e atualizar documentação, sem
alterar a árvore de código qualificada. O manifesto atual é
[human-central-manifest.json](human-central-manifest.json); os recibos antigos
`validation.json` e `final-validation.md` permanecem preservados.

## CI da revisão final

| Verificação | Resultado | Evidência |
|---|---|---|
| PostgreSQL 16/Redis reais e descartáveis | **4.188 passaram, 1 ignorado**, 123 avisos; 173,08 s | [CI](https://github.com/QGBALTIGO/orion-assistente/actions/runs/39450606857), job `118724084721` |
| Suíte em Docker Compose | **4.077 passaram, 112 ignorados**, 10 avisos; 80,12 s | Mesmo job, etapa Docker/Compose/health |
| Conector QR em Linux | **416 passaram, 0 falhas** | Job `118724085582` |
| Interface DOM | **306 passaram, 0 falhas** | Mesmo job do conector |
| Builds e smoke Baileys, proto/cache cifrado | **Aprovados**, sem sessão/rede WhatsApp | Mesmo job do conector |
| ASR, build e áudio sintético | **Aprovados** | Job `118724085308` |
| Upload de mídia | **Aprovado** | [CI de mídia](https://github.com/QGBALTIGO/orion-assistente/actions/runs/39450606801) |
| Upgrade, downgrade vazio, recusa com dados, backup/restore e health HTTP | **Aprovados** | Suíte e etapas do job PostgreSQL |

Os conjuntos se sobrepõem; suas contagens não devem ser somadas como testes únicos.

## Casos específicos validados

- Isolamento RLS, FKs compostas e recusa de acesso entre estabelecimentos.
- Duas pessoas disputando revisão/posse, geração obsoleta e transação de negócio
  que precisa concluir antes de uma transferência.
- QR com assistente desligada registra mensagem; nenhuma criação automática de
  cliente ou envio resulta da abertura da interface.
- Posse pessoal, permissão/revisão atuais, revogação de vínculo e alteração da
  versão de autenticação antes do transporte.
- Enfileiramento idempotente, dois workers concorrentes, timeout e recuperação
  sem repetição automática ou expiração artificial do slot.
- Pedido humano durante envio pendente suspende futuras respostas e conserva a
  operação em verificação, tanto pelo ingresso quanto pelo serviço de handoff.
- Meta com janela encerrada ou transporte desligado não chama o provedor;
  reabilitar não repete textos cancelados.
- Recibo Meta anterior à resposta HTTP, recibos fora de ordem e correlação
  assinada de operação após timeout, sem rebaixar leitura nem reenviar.
- Worker Meta prepara sob autoridade, bloqueia a outbox antiga e preserva o
  texto de eventos pendentes recebidos antes de habilitar a central, sem inventar
  horário do provedor ou nova janela de envio.
- Leitura pessoal monotônica, filtros de não lidas, histórico e reabertura de
  conversas em espera/encerradas para humano, sem retomada automática da IA.
- Edição concorrente de rascunhos de fluxo; simulação da revisão salva produz
  apenas uma trilha e mantém `enabled=false`, sem ações operacionais.

## Windows e navegador

A suíte local completa em `f883933` registrou **3.765 passaram, 416 ignorados e
quatro falhas de ambiente** já presentes na base, todas sem PostgreSQL local:
dois testes de contas, um de billing e um de schema restrito. Não foram omitidos
ou enfraquecidos. A CI descartável da revisão final executou essas verificações
com PostgreSQL/Redis e terminou aprovada.

O conector local registrou 412 aprovações e quatro casos POSIX ignorados; todos
os 416 passaram em Linux. A suíte DOM registrou 306 aprovações localmente e na CI.

Navegador Chromium sintético: 360, 768 e 1.440 px, temas claro e escuro, para
central, editor/simulador e CRM/respostas. Foram conferidos quebra de linha,
ausência de overflow horizontal, texto preservado após falha, retry com o mesmo
UUID, preparo factual e estados honestos de envio. Fontes locais Figtree/Outfit
foram usadas na rodada final. Rotas externas foram bloqueadas; as respostas de
API são sintéticas. Screenshots e resultados permanecem fora do Git em
`../orion-marcflow-evidence-20261020/central-browser-final-fonts` e
`../orion-marcflow-evidence-20261020/toolbox-browser-current` a partir da raiz do checkout.

O ensaio inicial do navegador usou uma página sem origem segura e falhou ao
gerar UUID. O teste passou a usar origem HTTPS sintética interceptada localmente,
sem requisição externa, preservando a API Web Crypto do navegador real.

## Limites

Nenhum deploy, migration operacional, alteração de sessão ou envio real ocorreu.
Flags continuam desligadas por padrão. Transportes são spies em memória ou
smokes offline; isso não comprova integração com conta Meta ou aparelho físico.

Mídia do atendimento, áudio de saída, importação de histórico, citação/reação,
grafo, executor/publicação de fluxos e homologação real permanecem pendentes.
A varredura profunda de segurança continua bloqueada pelo perfil de permissões
incompatível registrado na etapa anterior; não houve certificação de segurança.

A primeira CI desta continuação (`008e0d9`, run `39446140306`) teve quatro falhas,
corrigidas sem conceder UPDATE de tenants ao runtime ou remover asserções.
O diagnóstico e a decisão estão em [human-central.md](human-central.md).
