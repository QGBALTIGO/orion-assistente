# Conexão WhatsApp: administrador e empresas

O tutorial reutiliza o componente do administrador presente na versão `18a2939`:
cards de QR/Código com ícone e texto auxiliar, linha vertical de sete passos,
círculos, títulos, descrições e botão de continuar. Sua implementação é
compartilhada com as empresas no estilo central do painel, sem folha de estilo
independente nem uma segunda apresentação para os mesmos passos. Formulários e
ações continuam usando `os-button`, `os-large`, `os-form` e `os-field`.

Código e QR têm sete passos específicos, visíveis inicialmente. Continuar abre
o formulário ou prepara a exibição do QR; voltar permite reler o tutorial.
Abrir a página, mudar o método ou consultar o estado não inicia pareamento.
Código expirado é retirado da interface sem repetição automática da operação.
Atualizações periódicas preservam campos, foco e a etapa atual.

## Contrato das empresas

`POST /api/v1/tenants/{tenant_id}/qr-channels/{channel_id}/pair` aceita somente
`expected_revision` e `phone`, em dígitos com código do país. Proprietário,
empresa, revisão, canal habilitado e sessão iniciada são conferidos antes e
depois da operação. Não utiliza a conexão administrativa global.

O suporte operacional é descoberto por `runtime.capabilities.pair`. Com um
bridge antigo, a interface mantém QR e não anuncia Código como disponível.
Tentativas concorrentes compartilham uma solicitação por canal; uma falha
ambígua não dispara outra. O prazo máximo é 90 segundos.

## Preservação de sessões e saídas

Nenhuma migração, alteração de segredo, limpeza de credenciais ou recriação de
banco faz parte desta mudança. A simples retomada de credenciais registradas
não emite aviso de novo vínculo, mesmo sem recibo anterior. Um vínculo novo
conserva a preferência de aviso já existente, inclusive após `restartRequired`.
A intenção de novo vínculo existe somente na memória do processo.

## Qualificação

- Fixture HTTP restrita ao loopback, sem banco, credenciais ou provedor real.
- Testes de autorização, revisão, concorrência, expiração e respostas atrasadas.
- Navegador em 1440×900, 1366×768, 768×1024, 1024×768, 390×844 e 360×800,
  nos temas claro e escuro; Chromium e WebKit no workflow dedicado.
- A suíte completa de DOM tem seis falhas anteriores em
  `fields-panel-legacy.test.mjs`, reproduzidas no commit base `18a2939`:
  os testes ainda navegam para `cadastros`, que foi retirado do produto.
  O novo gate verifica os consumidores atuais sem reabrir essa rota.
- O harness VM antigo `qr-bridge/test/panel-ui.test.mjs` também mantém 67 falhas
  de contexto empresarial, com os mesmos 67 títulos na baseline `18a2939` e
  na versão candidata. Esses casos não constituem fluxos validados. O wizard
  atual, os testes DOM com sessão empresarial e o navegador têm gates próprios.

As capturas e os resultados finais de execução pertencem ao workflow e ao
registro de entrega. Emulação não comprova teclado virtual, iPhone físico,
entrega real de mensagens ou pareamento com uma conta real.

## WA-LIVE-01: preparação após QR expirado ou demorado

A bateria real encontrou o formulário administrativo com Gerar desabilitado e
sem orientação quando o estado era `qr_expired` ou `qr_delayed`. Esses estados
agora oferecem Preparar novo código, com o mesmo diálogo e os componentes do
painel. Cancelar não envia comandos; confirmar consulta novamente a conexão.
O caminho de preparação utiliza somente `reconnect`, preserva o telefone e o
tutorial, e aguarda `qr_ready`. Uma mudança para conexão ativa, pareamento,
revogação ou estado incerto interrompe a ação, sem escalar para `recover`.

Na versão `672747d`, administrador e empresa de demonstração geraram QR real
de 370 × 370 e código válido no formato de oito caracteres. Na empresa foram
conferidos o número canônico da solicitação, ocultar/reexibir sem novo POST e
expiração sem repetição automática. O dispositivo de teste foi desativado; o
cadastro anterior manteve revisão e preferências. Nenhum vínculo foi concluído
no aparelho. A tentativa administrativa terminou com fechamento de transporte
408/401 e teve o código retirado; o motivo detalhado não foi identificado.
Geração não constitui confirmação de vínculo nem teste de entrega no celular.
