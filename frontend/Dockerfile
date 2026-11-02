FROM node:26-alpine@sha256:c6ad9e9c1d80f6eb607c26b4b0a132ff7a2344fad1c78ea3baa2a7d4c8de6d94 AS build

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
