# ── Stage 1: Build ────────────────────────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /app
COPY package*.json ./
RUN npm ci

COPY . .

# VITE_API_URL is baked into the static bundle at build time.
# Must be set explicitly per deployment — no default so misconfiguration fails loudly.
ARG VITE_API_URL
ENV VITE_API_URL=$VITE_API_URL

# Client dashboard origin shown to staff when they issue credentials.
ARG VITE_DASHBOARD_URL
ENV VITE_DASHBOARD_URL=$VITE_DASHBOARD_URL

RUN npm run build

# ── Stage 2: Serve ────────────────────────────────────────────────────────────
FROM nginx:1.27-alpine

# apache2-utils provides htpasswd for basic auth
RUN apk add --no-cache apache2-utils

# nginx templates feature: envsubst replaces ${PORT}, ${API_HOST} and
# ${NGINX_LOCAL_RESOLVERS} at startup
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

# Runs before envsubst and guarantees NGINX_LOCAL_RESOLVERS is defined.
# docker-entrypoint.sh only sources *.envsh files that are executable, and the
# bit does not survive a checkout on every platform, so set it here.
COPY docker-entrypoint.d/19-resolvers.envsh /docker-entrypoint.d/19-resolvers.envsh
RUN chmod +x /docker-entrypoint.d/19-resolvers.envsh

# .htpasswd is committed — change the password by regenerating this file:
#   htpasswd -nb admin <new-password>
COPY .htpasswd /etc/nginx/.htpasswd

COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
