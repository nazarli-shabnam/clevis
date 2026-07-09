FROM oven/bun:1-alpine AS builder
WORKDIR /app

COPY package.json bun.lock ./
RUN bun install --frozen-lockfile

COPY . .

# NEXT_PUBLIC_* vars are baked at build time — must be passed as build args
ARG NEXT_PUBLIC_API_BASE
ARG NEXT_PUBLIC_DEFAULT_ROLE=viewer
ARG NEXT_PUBLIC_GITHUB_APP_SLUG
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
ENV NEXT_PUBLIC_DEFAULT_ROLE=$NEXT_PUBLIC_DEFAULT_ROLE
ENV NEXT_PUBLIC_GITHUB_APP_SLUG=$NEXT_PUBLIC_GITHUB_APP_SLUG

RUN bun run build

# ── runner ────────────────────────────────────────────────────────────────────
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
# Bind to all interfaces so Traefik can reach the server via the Docker network.
# Without this Next.js standalone defaults to localhost (127.0.0.1), which is
# unreachable from other containers.
ENV HOSTNAME=0.0.0.0

# outputFileTracingRoot resolves to "/" in Docker, so Next.js mirrors the full
# path and nests standalone output under app/ (i.e. server.js is at
# standalone/app/server.js, not standalone/server.js).
COPY --from=builder --chown=node:node /app/.next/standalone/app ./
COPY --from=builder --chown=node:node /app/.next/static         ./.next/static

USER node
EXPOSE 3000
CMD ["node", "server.js"]
