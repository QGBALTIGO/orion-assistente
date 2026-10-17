#!/usr/bin/env bash
set -euo pipefail
sudo apt-get update -qq
sudo apt-get install -y -qq espeak-ng ffmpeg
espeak-ng -v pt-br -s 130 -w /tmp/orion-ci-voice.wav "Olá, Orion. Este é um teste de transcrição de áudio."
test -s /tmp/orion-ci-voice.wav
ffmpeg -hide_banner -loglevel error -y -i /tmp/orion-ci-voice.wav -c:a libopus -b:a 20k /tmp/orion-ci-voice.ogg
test -s /tmp/orion-ci-voice.ogg
docker run --rm \
  -v /tmp/orion-ci-voice.wav:/tmp/voice.wav:ro \
  -v /tmp/orion-ci-voice.ogg:/tmp/voice.ogg:ro \
  --entrypoint python orion-faster-whisper:ci -c '
import app
app._model=app._create_model()
for filename in ("/tmp/voice.wav","/tmp/voice.ogg"):
    audio=open(filename,"rb").read()
    seconds=app._audio_duration(audio)
    transcript=app._recognize(audio,"pt")
    assert 0.5 < seconds < 20, seconds
    assert transcript.strip(), "silent transcription"
    # TTS espeak is not a dependable Portuguese ASR-quality reference.
    # This smoke checks the decoding/inference path, not word accuracy.
    assert len(transcript.strip()) >= 2, "empty decoded transcript"
    assert all(ch.isprintable() or ch in "\n\t" for ch in transcript), "invalid text"
    print("ORION_FASTER_WHISPER_REAL_AUDIO_PIPELINE_OK: "+filename.rsplit(".",1)[-1])
'
