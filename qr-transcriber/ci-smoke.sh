#!/usr/bin/env bash
set -euo pipefail
sudo apt-get update -qq
sudo apt-get install -y -qq espeak-ng
espeak-ng -v pt-br -s 130 -w /tmp/orion-ci-voice.wav "Olá, Orion. Este é um teste de transcrição de áudio."
test -s /tmp/orion-ci-voice.wav
docker run --rm -v /tmp/orion-ci-voice.wav:/tmp/voice.wav:ro --entrypoint python orion-faster-whisper:ci -c '
import app
audio=open("/tmp/voice.wav","rb").read()
app._model=app._create_model()
seconds=app._audio_duration(audio)
transcript=app._recognize(audio,"pt")
assert 0.5 < seconds < 20, seconds
assert transcript.strip(), "silent transcription"
print("ORION_FASTER_WHISPER_REAL_AUDIO_OK")
'
