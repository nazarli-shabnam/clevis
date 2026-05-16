FROM python:3.12-slim
WORKDIR /app
COPY apps/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/api/src ./src
COPY apps/api/alembic ./alembic
COPY apps/api/alembic.ini .
COPY apps/api/entrypoint.sh .
COPY packages/checks /app/packages/checks
ENV PYTHONPATH=/app:/app/packages/checks/src
RUN chmod +x entrypoint.sh
ENTRYPOINT ["./entrypoint.sh"]
