FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 HF_HOME=/models
WORKDIR /srv/asr
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
RUN adduser --disabled-password --gecos '' asr && mkdir -p /models && chown -R asr:asr /models
USER asr
EXPOSE 8000
CMD ["uvicorn","app:app","--host","::","--port","8000","--workers","1"]
