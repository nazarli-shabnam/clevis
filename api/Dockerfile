FROM python:3.12-slim
WORKDIR /app
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY api/src ./src
COPY api/alembic ./alembic
COPY api/alembic.ini .
COPY packages/checks /app/packages/checks
ENV PYTHONPATH=/app:/app/packages/checks/src
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
