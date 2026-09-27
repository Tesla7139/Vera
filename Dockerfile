FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY vera ./vera
ENV PORT=8080
# One worker on purpose: all context and conversation state lives in process memory.
CMD ["sh", "-c", "uvicorn vera.app:app --host 0.0.0.0 --port ${PORT} --workers 1"]
