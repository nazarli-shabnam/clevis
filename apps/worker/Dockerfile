FROM python:3.12-slim
WORKDIR /app
COPY apps/worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY packages/checks /app/packages/checks
RUN pip install --no-cache-dir /app/packages/checks
COPY apps/worker/src ./src
COPY apps/worker/entrypoint.sh .
ENV PYTHONPATH=/app/src
# a+rX: COPY keeps the checkout's file modes, so a strict umask (0600/0700) must not make the
# source unreadable to the unprivileged user below.
RUN chmod +x entrypoint.sh && chmod -R a+rX /app
# Run as an unprivileged user: the only files the worker writes are its heartbeat files in
# /tmp (checked by the compose healthcheck). Source and site-packages stay root-owned, and
# bytecode isn't written at runtime since /app isn't writable.
ENV PYTHONDONTWRITEBYTECODE=1
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app
USER app
ENTRYPOINT ["./entrypoint.sh"]
