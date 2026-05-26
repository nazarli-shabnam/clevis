FROM node:22-alpine AS builder
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# NEXT_PUBLIC_* vars are baked at build time — must be passed as build args
ARG NEXT_PUBLIC_API_BASE
ARG NEXT_PUBLIC_DEFAULT_ROLE=viewer
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
ENV NEXT_PUBLIC_DEFAULT_ROLE=$NEXT_PUBLIC_DEFAULT_ROLE

RUN npm run build

# ── runner ────────────────────────────────────────────────────────────────────
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production

# outputFileTracingRoot resolves to "/" in Docker, so Next.js nests the
# standalone output under /app/.next/standalone/app/ (mirroring the /app path).
COPY --from=builder /app/.next/standalone/app ./
COPY --from=builder /app/.next/static         ./.next/static

EXPOSE 3000
CMD ["node", "server.js"]
