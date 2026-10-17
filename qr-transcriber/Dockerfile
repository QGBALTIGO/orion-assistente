FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/models \
    QR_ASR_MODEL_DIR=/models \
    QR_ASR_MODEL=base \
    QR_ASR_CPU_THREADS=2 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    OMP_NUM_THREADS=2
WORKDIR /srv/asr
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Download multilingual model at image build, not when private audio arrives.
# Build fails if it cannot be fetched, rather than serving a fake "ready" status.
RUN mkdir -p /models && python -c "from faster_whisper import WhisperModel; WhisperModel('base',device='cpu',compute_type='int8',download_root='/models')"
COPY app.py .
RUN adduser --disabled-password --gecos '' asr && chown -R asr:asr /models /srv/asr
USER asr
EXPOSE 8000
CMD ["uvicorn","app:app","--host","::","--port","8000","--workers","1"]
