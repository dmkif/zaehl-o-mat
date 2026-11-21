FROM node:26-alpine@sha256:6bd9ab43c0ec0190ca7b2d193906cb8b7edf6e7f4afa43ddeccd6452710bffb5 AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM ghcr.io/static-web-server/static-web-server:2@sha256:23be6c04d474979d6e738081918f5874f88502a150d1123fd000719101a0f05c

COPY --from=build /app/dist /public

ENV SERVER_ROOT=/public \
    SERVER_PORT=8080 \
    SERVER_FALLBACK_PAGE=/public/index.html

# Non-root (the chart runs it as 65534 as well); port 8080 needs no privilege.
# No HEALTHCHECK: the image is FROM scratch (no shell or client to probe with).
USER 65534:65534
EXPOSE 8080
