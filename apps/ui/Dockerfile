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

# server.js is always at the root of .next/standalone/ regardless of
# outputFileTracingRoot. Copy the whole standalone dir so server.js lands at ./
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static     ./.next/static

EXPOSE 3000
CMD ["node", "server.js"]
