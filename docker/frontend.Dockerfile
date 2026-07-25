# Build the Float frontend using Node 20
ARG NODE_BASE=mcr.microsoft.com/devcontainers/javascript-node:20
FROM ${NODE_BASE} AS build

WORKDIR /app

# Install from the repository-root workspace lockfile.
COPY package.json package-lock.json ./
COPY frontend/package.json ./frontend/package.json
RUN npm ci --workspace frontend

COPY frontend/ ./frontend/
RUN npm run build --workspace frontend

ARG NGINX_BASE=mcr.microsoft.com/oss/nginx/nginx:1.25-alpine
FROM ${NGINX_BASE} AS runtime

RUN apk add --no-cache curl

COPY --from=build /app/frontend/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD curl --fail http://localhost/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
