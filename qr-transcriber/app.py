"""Optional *private* open-source transcription service; not deployed by default."""
from io import BytesIO
import os
import hmac
from fastapi import FastAPI, Request, HTTPException
from faster_whisper import WhisperModel

app = FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
model = None

@app.get("/health")
async def health():
    return {"status":"ok","model_loaded":model is not None}

@app.post("/transcribe")
async def transcribe(request: Request):
    key=os.environ.get("QR_ASR_TOKEN","")
    supplied=request.headers.get("x-orion-asr-key","")
    if len(key)<32 or not hmac.compare_digest(key,supplied):
        raise HTTPException(401,"unauthorized")
    if request.headers.get("content-type")!="application/octet-stream":
        raise HTTPException(415,"unsupported")
    length=request.headers.get("content-length")
    if length and (not length.isdigit() or int(length)>2_000_000):
        raise HTTPException(413,"too large")
    data=await request.body()
    if not 0<len(data)<=2_000_000:
        raise HTTPException(413,"too large")
    language=request.headers.get("x-orion-audio-language","auto")
    if language not in ("auto","pt","en","es"):
        raise HTTPException(422,"language")
    global model
    if model is None:
        model=WhisperModel(os.environ.get("QR_ASR_MODEL","small"),
                           device="cpu",compute_type="int8")
    try:
        segments, info=model.transcribe(BytesIO(data),language=None if language=="auto" else language,
                                        vad_filter=True,beam_size=1)
        snippets=[]
        for segment in segments:
            if segment.end>120:break
            snippets.append(segment.text)
            if len(" ".join(snippets))>2000:break
        return {"text":" ".join(snippets).strip()[:2000]}
    except Exception:
        raise HTTPException(422,"audio could not be processed")
