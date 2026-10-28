# Conexão WhatsApp: administrador e empresas

O formulário usa os componentes existentes do painel: `os-button`, `os-large`,
`os-card`, `os-form`, `os-field`, `os-service-option` e `os-more-fields`.
Não há uma folha de estilo independente para o conector. Os canais conservam
somente regras de organização e adaptação à largura útil do componente.

Código e QR têm sete passos específicos, visíveis inicialmente e recolhíveis.
Abrir a página, mudar o método ou consultar o estado não inicia pareamento.
Código expirado é retirado da interface sem repetição automática da operação.
Atualizações periódicas preservam campos, foco e tutoriais abertos ou fechados.

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

As capturas e os resultados finais de execução pertencem ao workflow e ao
registro de entrega. Emulação não comprova teclado virtual, iPhone físico,
entrega real de mensagens ou pareamento com uma conta real.
