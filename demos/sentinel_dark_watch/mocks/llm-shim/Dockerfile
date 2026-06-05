FROM python:3.12-slim

RUN pip install --no-cache-dir fastapi uvicorn pydantic

WORKDIR /app
COPY app.py .

EXPOSE 41001

HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:41001/health')" || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "41001"]
