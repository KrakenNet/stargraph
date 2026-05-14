# SPDX-License-Identifier: Apache-2.0
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.115.* uvicorn[standard]==0.32.* pydantic==2.*
COPY app.py /app/app.py
EXPOSE 41001
HEALTHCHECK --interval=5s --timeout=3s --retries=10 \
    CMD curl -f http://localhost:41001/health || exit 1
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "41001"]
