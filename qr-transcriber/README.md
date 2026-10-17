# Orion — Transcrição privada com faster-whisper

Serviço de reconhecimento de fala **opcional** do Orion. Recebe somente áudio do
`orion-qr-bridge` por rede privada Railway. Não há endpoint público, chatbot,
envio automático de mensagens nem compartilhamento de mídia com APIs externas.

## Imagem e recursos

A imagem em `qr-transcriber/Dockerfile` instala `faster-whisper==1.2.1`,
`av>=11,<18` (compatibilidade da interface `metadata_errors`),
e pré-baixa o modelo multilíngue **base** no build. Na execução, o modelo é
carregado de `/models` em CPU/int8, sem dependência de internet.
Configure cerca de 1 GB ou mais de RAM para ter margem e monitore CPU,
uso real de RAM e latência antes de ampliar o tráfego.

```bash
docker build -t orion-faster-whisper ./qr-transcriber
docker run --rm -e QR_ASR_TOKEN=<token-aleatorio-de-32-ou-mais-caracteres> \
  -p 127.0.0.1:8000:8000 orion-faster-whisper
```

O endpoint de saúde é `GET /health`, que retorna HTTP 200 **somente depois**
que o modelo foi carregado. A rota `POST /transcribe` requer
`X-Orion-ASR-Key` e `Content-Type: application/octet-stream`, com
`X-Orion-Audio-Language: auto|pt|en|es`. Limites: 2 MB, 120 segundos de
áudio decodificado, 2000 caracteres de resposta e somente 1 inferência
simultânea por réplica. Mídia não é gravada no disco do serviço.

## Testes

O GitHub Actions executa as suítes de API, o build Docker completo e um
**teste com modelo real**, no qual o eSpeak gera um WAV em português brasileiro.
O áudio sintético é transcrito em CPU, usando apenas o modelo dentro da imagem,
e o pipeline reprova se não houver texto. Não utiliza áudio de clientes.

## Ativação na Railway (após liberar recursos)

1. Adicionar serviço privado `orion-asr` com fonte GitHub
   `QGBALTIGO/orion-assistente`, diretório raiz `/qr-transcriber`,
   Dockerfile `Dockerfile`, healthcheck `/health`, porta interna `8000`,
   sem domínio público. Não adicionar volume de sessão do WhatsApp.
2. Criar um token randômico de pelo menos 32 caracteres e configurar
   `QR_ASR_TOKEN` **tanto** em `orion-asr` quanto em `orion-qr-bridge`.
   Não divulgar o valor em logs, PR ou chat.
3. No `orion-qr-bridge`, configurar
   `QR_ASR_URL=http://orion-asr.railway.internal:8000/transcribe`
   (verificar o hostname privado real na Railway).
4. Confirmar status `featureCapabilities.transcription=true` pelo endpoint
   autenticado já existente, antes de ativar a chave **Transcrição de áudio**
   em Configurações no painel. Essa opção é deliberadamente desligada por
   padrão. Revalidar um áudio controlado pela conta dona do número.
5. Monitorar volume de transcrições, recursos e latência. Não reiniciar nem
   reconectar a sessão Baileys para habilitar um recurso opcional.

Se o plano impedir um novo serviço, **não** adicionar o ASR ao processo da
API pública nem sobrecarregar o conector do WhatsApp. A imagem e a CI
continuam disponíveis até haver capacidade de provisionamento.
