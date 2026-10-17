FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM ghcr.io/static-web-server/static-web-server:2@sha256:94618e3d17802265a21926217e6f9e7832b311b6fbf5933684001739fafed06a

COPY --from=build /app/dist /public

ENV SERVER_ROOT=/public \
    SERVER_PORT=8080 \
    SERVER_FALLBACK_PAGE=/public/index.html

# Non-root (the chart runs it as 65534 as well); port 8080 needs no privilege.
# No HEALTHCHECK: the image is FROM scratch (no shell or client to probe with).
USER 65534:65534
EXPOSE 8080
