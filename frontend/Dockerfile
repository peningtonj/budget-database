FROM node:22-slim AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# No VITE_API_BASE set here deliberately — api.js's own production default
# (window.location.origin + "/api") now handles this correctly regardless
# of which port actually served the page, which a fixed build-time value
# can't do for a site reachable on more than one external port.
RUN npm run build

FROM caddy:2
COPY --from=builder /app/dist /srv
COPY Caddyfile /etc/caddy/Caddyfile
EXPOSE 80
