FROM python:3.12-slim
WORKDIR /app
COPY apps/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/api/src ./src
COPY apps/api/alembic ./alembic
COPY apps/api/alembic.ini .
COPY apps/api/entrypoint.sh .
COPY packages/checks /app/packages/checks
RUN pip install --no-cache-dir /app/packages/checks
RUN chmod +x entrypoint.sh
# Run as an unprivileged user: nothing here writes outside /tmp, and 8080 is above 1024.
# Source and site-packages stay root-owned (read-only to the app), and bytecode isn't written
# at runtime since /app isn't writable.
ENV PYTHONDONTWRITEBYTECODE=1
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app
USER app
EXPOSE 8080
ENTRYPOINT ["./entrypoint.sh"]
