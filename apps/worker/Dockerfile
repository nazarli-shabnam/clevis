FROM python:3.12-slim
WORKDIR /app
COPY apps/worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/worker/src ./src
COPY apps/worker/entrypoint.sh .
COPY packages/checks /app/packages/checks
RUN pip install --no-cache-dir /app/packages/checks
ENV PYTHONPATH=/app/src
RUN chmod +x entrypoint.sh
ENTRYPOINT ["./entrypoint.sh"]
