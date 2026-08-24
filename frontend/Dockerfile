FROM node:22-slim AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Vite bakes import.meta.env.VITE_* in at build time. api.js does
# `new URL(`${API_BASE}/...`)` with no base argument, which throws
# "Invalid URL" for a relative string — this must be absolute.
ENV VITE_API_BASE=https://aus-budget.duckdns.org/api
RUN npm run build

FROM caddy:2
COPY --from=builder /app/dist /srv
COPY Caddyfile /etc/caddy/Caddyfile
EXPOSE 80
