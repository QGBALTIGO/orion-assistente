# Dispositivos de WhatsApp por empresa

O proprietário abre **Conectar WhatsApp** no topo do menu. A central permite
adicionar dispositivos, conectar por QR e acompanhar conexão e assistente
separadamente. A primeira confirmação habilita e inicia apenas o dispositivo
selecionado; a assistente exige uma autorização própria. Abrir a página nunca
inicia sessões. Os dados de serviços, valores, profissionais e expediente são
consultados no catálogo atual da própria empresa, sem duplicação por dispositivo.

O cadastro continua privado, com até dez contatos autorizados. O administrador
mantém seu conector operacional existente; o atalho de uma conta empresarial
nunca abre rotas ou credenciais globais do administrador. Editores e leitores
não recebem autorização de proprietário por visualizar a mesma central.

## Aviso e retomada

Após um socket realmente conectar, o destino do aviso vem exclusivamente da
identidade autenticada desse socket. O aviso usa o mesmo envio com proto salvo
antes da transmissão e um recibo exclusivo no volume. Reconexões e reinícios não
repetem a tentativa, inclusive quando a entrega anterior é incerta. Isso não
permite mensagens arbitrárias para o próprio número no endpoint de envio.

Somente dispositivos que efetivamente conectaram são lembrados. O índice de
retomada é cifrado em `/data/orion-channels/resume` com uma chave derivada de
`QR_BRIDGE_TOKEN`; não há credenciais de canal em texto puro. Preserve a chave e
o volume. O processo tenta retomar na inicialização e a cada 30 segundos, com
varreduras serializadas e a capacidade original do gerenciador. Cada tentativa
revalida empresa, proprietário, canal habilitado e posse exclusiva no banco.
Uma retomada exige credenciais registradas; nunca cria um QR novo em segundo
plano. **Parar conexão** remove a intenção persistida. Logout, revogação ou
desativação não concedem uma nova autorização. Erros de rede preservam a intenção;
índice corrompido ou chave diferente falham fechados sem sobrescrever evidências.

Dispositivos conectados antes desta atualização entram no índice após uma
conexão explícita pela central; não se enumeram diretórios antigos nem se
fabricam autorizações. O conector principal continua usando sua retomada existente.

## Homologação

Testes cobrem o fluxo habilitar/iniciar com revisão atual, troca de empresa
durante a requisição, resultado incerto sem repetição, QR temporário, ausência de
chave interna no DOM, envio próprio único e retomada sem ativar a assistente.
Antes de afirmar aceitação real, conectar dois números autorizados, conferir o
aviso no aparelho e reiniciar o conector para observar a retomada sem QR nem
notificação duplicada. Testes de API/carga não substituem essa conferência.
