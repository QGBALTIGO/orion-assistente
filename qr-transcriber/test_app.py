"""No real WhatsApp audio is needed for the ASR boundary test."""
import asyncio
from io import BytesIO
import math
import struct
import wave

import httpx
import pytest

import app as svc


TOKEN = "ci-asr-secret-long-enough-not-for-production"


def wave_bytes(seconds=0.25):
    f=BytesIO()
    with wave.open(f,"wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        data=[int(1800*math.sin(i*2*math.pi*440/16000)) for i in range(int(seconds*16000))]
        wav.writeframes(b"".join(struct.pack("<h",sample) for sample in data))
    return f.getvalue()


class FakeModel:
    def __init__(self):
        self.calls=[]
    def transcribe(self,audio,**kwargs):
        assert audio.read(4)==b"RIFF"
        self.calls.append(kwargs)
        return iter([type("Segment",(),{"end":0.2,"text":" Olá, Orion! "})()]), object()


@pytest.mark.asyncio
async def test_transcribe_private_opt_in_and_limits(monkeypatch):
    monkeypatch.setenv("QR_ASR_TOKEN",TOKEN)
    fake=FakeModel()
    monkeypatch.setattr(svc,"_model",fake)
    audio=wave_bytes()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=svc.app),base_url="http://asr.railway.internal"
    ) as client:
        assert (await client.get("/health")).json()=={"status":"ok","model_loaded":True}
        assert (await client.post("/transcribe",content=audio)).status_code==401
        headers={"X-Orion-ASR-Key":"invalid","Content-Type":"application/octet-stream"}
        assert (await client.post("/transcribe",content=audio,headers=headers)).status_code==401
        headers["X-Orion-ASR-Key"]=TOKEN
        headers["Content-Type"]="text/plain"
        assert (await client.post("/transcribe",content=audio,headers=headers)).status_code==415
        headers["Content-Type"]="application/octet-stream"
        headers["X-Orion-Audio-Language"]="ja"
        assert (await client.post("/transcribe",content=audio,headers=headers)).status_code==422
        headers["X-Orion-Audio-Language"]="pt"
        assert (await client.post("/transcribe",content=b"",headers=headers)).status_code==422
        assert (await client.post("/transcribe",content=b"A"*(svc.MAX_AUDIO_BYTES+1),headers=headers)).status_code==413
        ok=await client.post("/transcribe",content=audio,headers=headers)
        assert ok.status_code==200,ok.text
        assert ok.json()=={"text":"Olá, Orion!"}
        assert len(fake.calls)==1
        assert fake.calls[0]["language"]=="pt"
        assert fake.calls[0]["vad_filter"] is True
        assert fake.calls[0]["beam_size"]==1


@pytest.mark.asyncio
async def test_reject_corrupt_and_oversized_duration(monkeypatch):
    monkeypatch.setenv("QR_ASR_TOKEN",TOKEN)
    monkeypatch.setattr(svc,"_model",FakeModel())
    headers={"X-Orion-ASR-Key":TOKEN,"Content-Type":"application/octet-stream"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=svc.app),base_url="http://asr.railway.internal"
    ) as client:
        response=await client.post("/transcribe",content=b"not a sound file",headers=headers)
        assert response.status_code==422
    monkeypatch.setattr(svc,"MAX_AUDIO_SECONDS",0.1)
    with pytest.raises(ValueError):
        svc._audio_duration(wave_bytes(seconds=0.25))


@pytest.mark.asyncio
async def test_health_fails_closed_when_model_not_loaded(monkeypatch):
    monkeypatch.setattr(svc,"_model",None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=svc.app),base_url="http://asr.railway.internal"
    ) as client:
        assert (await client.get("/health")).status_code==503


@pytest.mark.asyncio
async def test_busy_worker_returns_503_not_unbounded_queue(monkeypatch):
    monkeypatch.setenv("QR_ASR_TOKEN",TOKEN)
    monkeypatch.setattr(svc,"_model",FakeModel())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=svc.app),base_url="http://asr.railway.internal"
    ) as client:
        await svc._transcribe_lock.acquire()
        try:
            response=await client.post("/transcribe",content=wave_bytes(),headers={
                "X-Orion-ASR-Key":TOKEN,"Content-Type":"application/octet-stream"})
            assert response.status_code==503
        finally:
            svc._transcribe_lock.release()
