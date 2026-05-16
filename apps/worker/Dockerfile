FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY entrypoint.sh .
ENV PYTHONPATH=/app/src
RUN chmod +x entrypoint.sh
ENTRYPOINT ["./entrypoint.sh"]
