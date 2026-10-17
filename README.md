# Zähl-O-Mat

A self-hosted utility meter management application. Take a photo of any meter (electricity, water, gas, oil), and the app extracts the reading via a local Ollama vision model or an optional OCR service. Readings are stored per property, visualized in charts, and the oil heating price is fetched daily for cost calculations.

## Features

- Multi-tenant: properties → meters → readings hierarchy
- Reading extraction: Ollama vision model (primary, GPU-accelerated) with an optional, separately-deployed EasyOCR service as a fallback
- Serial number detection — warns when the photo doesn't match the registered meter
- Oil heating price tracking (daily cronjob, heizoel-aktuell source)
- Dashboard with consumption overview, daily averages per meter, meter type aggregates, and heating oil buy signal
- Admins and superadmins can edit or delete individual readings directly in the meter detail view
- Role-based access: `superadmin` / `admin` / `manager` / `user`
- SSO via OIDC (Authentik or any OpenID Connect provider) with PKCE
- Helm chart for Kubernetes deployment

## Quickstart (Docker / Podman Compose)

```bash
git clone https://github.com/dmkif/zaehl-o-mat.git
cd zaehl-o-mat

# Pull the Ollama vision model (required once)
docker run --rm -it ollama/ollama ollama pull gemma4:e4b

# Start the stack
docker compose up -d
```

The app is available at **http://localhost:8081** (Compose proxy; plain HTTP, local use only).  
There are no default credentials. Before the first start, create a `.env` file
next to `docker-compose.yaml` with freshly generated secrets (Compose refuses to
start without them):

```bash
cat > .env <<EOF
JWT_SECRET_KEY=$(openssl rand -base64 48)
SUPERADMIN_USER=admin
SUPERADMIN_PASSWORD=$(openssl rand -base64 18)
POSTGRES_PASSWORD=$(openssl rand -hex 24)
EOF
```

Log in with `SUPERADMIN_USER` / `SUPERADMIN_PASSWORD` from that file. The backend
rejects placeholder or weak values at startup: `JWT_SECRET_KEY` needs at least
32 characters, `SUPERADMIN_PASSWORD` at least 12, neither may be a well-known
value (`changeme`, `secret`, `password`, `admin123`, ...), and user and password
must be set together or both left empty. `DEBUG` defaults to `false`; set
`DEBUG=true` in `.env` for local development only.

`POSTGRES_PASSWORD` is required too. An existing `db-data` volume keeps the password
it was created with: put that value in `.env` (or change it in the database with
`ALTER USER`). Ollama is not published on the host (it has no authentication); the
backend (127.0.0.1:8000) is only reachable from the host itself. Use the proxy on
port 8081.

> **Note:** The stack includes an Ollama container (and the optional `ocr`
> container) that expect an NVIDIA GPU. Without a GPU, remove the
> `devices:` block from the relevant service in `docker-compose.yaml` and
> set `OLLAMA_MODEL` to a small CPU-capable model. The `ocr` service and
> its `OCR_URL` line are entirely optional — comment both out if you only
> want the LLM path (see "OCR Service" below).

## Environment Variables

All variables are set on the `backend` service.

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://...` | PostgreSQL connection string |
| `JWT_SECRET_KEY` | *(required)* | Secret for signing JWT tokens, at least 32 random characters (`openssl rand -base64 48`); weak/placeholder values are rejected at startup |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Token lifetime in minutes |
| `SUPERADMIN_USER` | `""` | Username for the built-in superadmin account |
| `SUPERADMIN_PASSWORD` | `""` | Password for the built-in superadmin account, at least 12 characters, not a common password; must be set together with `SUPERADMIN_USER` |
| `UPLOAD_PATH` | `/data/uploads` | Directory where meter images are stored |
| `APP_BASE_URL` | `https://zaehl-o-mat.example.com` | Public base URL (used for CORS + OIDC) |
| `DEBUG` | `false` | Enable debug logging |
| `TRUSTED_PROXIES` | loopback + RFC1918 | Comma-separated CIDRs of reverse proxies whose `X-Forwarded-For` is believed. Rate limiting keys on the rightmost forwarded address not in this list (client-supplied entries are ignored); `*` is rejected. The default trusts every private address (works behind the compose `proxy` / Traefik, but any pod/container on the private network could spoof its IP) - in production narrow it to the proxy's subnet / Traefik pod CIDR. uvicorn runs with `--no-proxy-headers`; the limiter evaluates the header itself. |
| **KI-Gateway** (alternative to Ollama) | | |
| `OCR_BACKEND` | `ollama` | `ollama` (direct Ollama API) or `gateway` (OpenAI-compatible KI-Gateway). |
| `GATEWAY_BASE_URL` | `""` | Required for `gateway`, e.g. `http://ai-gateway.ai-gateway.svc:20128/v1`. |
| `GATEWAY_API_KEY` | – | Required for `gateway`. Bearer key (secret store only, never logged). |
| `GATEWAY_PROFILE` | `local-only` | Sent as `model`. Use a local-only profile; a key without cloud connection keeps images local. |
| `GATEWAY_TIMEOUT_S` | `300` | Client timeout. Values below 300 abort start-up (cold start / GPU wake-up takes up to ~2 min). |
| `MODEL_PROFILE` | per backend | `qwen3.5-9b` (gateway default), `qwen3-vl-8b`, `gemma4-e4b` (Ollama default): image edge, thinking mode, prompt. |
| `PROMPT_DIR` | `""` | Directory with `<profile>.txt` prompt files overriding the packaged ones in `backend/app/prompts/`. |
| **Ollama** | | |
| `OLLAMA_URL` | `""` | Ollama API base URL (e.g. `http://ollama:11434`). Leave empty to disable. |
| `OLLAMA_MODEL` | `gemma4:e4b` | Vision model name |
| `OLLAMA_API_KEY` | `""` | Bearer token for hosted/proxied Ollama endpoints. Not needed for a local, unauthenticated Ollama. |
| **OCR (optional)** | | |
| `OCR_URL` | `""` | OCR service base URL (e.g. `http://ocr:8100`). Leave empty to disable — the app runs fine on the LLM path alone. |
| `OCR_API_KEY` | `""` | Shared secret: the backend sends it as Bearer token, the OCR service (same variable) requires it. Required by Compose and the chart; empty = unauthenticated OCR service. |
| **Oil price** | | |
| `OIL_PRICE_SOURCE` | `heizoel-aktuell` | Price source: `heizoel-aktuell` \| `tankerkoenig` \| `custom` |
| `OIL_PRICE_API_KEY` | `""` | API key for tankerkoenig or custom source |
| `OIL_PRICE_API_URL` | `""` | Endpoint URL for a custom price source |
| **OIDC** | | |
| `OIDC_CLIENT_ID` | `""` | OIDC client ID — leave empty to disable SSO |
| `OIDC_CLIENT_SECRET` | `""` | OIDC client secret |
| `OIDC_DISCOVERY_URL` | `""` | OpenID Connect discovery document URL |
| `OIDC_REDIRECT_URI` | `""` | Callback URL registered with your identity provider |
| `OIDC_ADMIN_GROUP` | `admin` | OIDC group mapped to the `admin` role |
| `OIDC_MANAGER_GROUP` | `verwalter` | OIDC group mapped to the `manager` role |
| `OIDC_USER_GROUP` | `user` | OIDC group mapped to the `user` role |
| `OIDC_GROUP_DIAGNOSTICS` | `false` | Log delivered vs. expected group names when an SSO sign-in is rejected (sensitive; enable only while investigating, warns at start-up) |
| `OIDC_RATE_LIMIT` | `20/minute` | Rate limit per client for `/api/auth/login` and `/api/auth/callback` |
| `OIDC_MAX_PENDING_STATES` | `1000` | Cap on pending (unfinished) SSO logins; above it `/auth/login` answers 503 |
| `OIDC_HTTP_TIMEOUT_S` | `10` | Timeout in seconds for calls to the identity provider (connect is capped at 3 s) |
| **API** | | |
| `ENABLE_API_DOCS` | `false` | Serve `/api/docs`, `/api/redoc` and `/api/openapi.json` (off by default) |

### Secrets from Files

Instead of environment variables the backend can read secrets from files mounted
at `/run/secrets/`.  Each file name corresponds to a setting name in lower-case
(e.g. `/run/secrets/database_url`).  This is the standard Kubernetes projected-
volume pattern and removes the need for `envFrom` secret refs.

Environment variables take precedence over file-based secrets.

**Helm chart example** (values.yaml):

```yaml
backend:
  secretVolume:
    enabled: true
    secretName: zaehl-o-mat-secrets   # existing k8s Secret with one key per setting
```

## OIDC Setup (Authentik)

1. Create an **OAuth2/OpenID Connect** provider in Authentik.
2. Set the redirect URI to `https://<your-domain>/api/auth/callback` (the backend handles the callback, then redirects the browser to the SPA route `/auth/token#code=...`; the SPA uses history-mode routing, not hash routing). Register exactly this URL, never a wildcard.
3. Copy the client ID, secret and discovery URL into the env vars above.
4. Create groups matching `OIDC_ADMIN_GROUP` / `OIDC_MANAGER_GROUP` / `OIDC_USER_GROUP` and assign users.

When `OIDC_CLIENT_ID` is set, the login page shows a **"Login with SSO"** button in addition to the local superadmin login.

## KI-Gateway (local models only)

With `OCR_BACKEND=gateway` the backend calls an OpenAI-compatible gateway (`POST {GATEWAY_BASE_URL}/chat/completions`,
image first, `temperature 0`, `enable_thinking: false`, no `response_format`; the answer text is parsed as JSON).
There is never a cloud fallback: if no local model answers, the scan fails with HTTP 503
"Lokales Modell nicht erreichbar, bitte später erneut versuchen" and can be repeated; HTTP 401/403/4xx from the
gateway become HTTP 502 "Konfigurationsfehler beim KI-Gateway". Bulk scans stop sending images after the first such error.
Per-model prompts and image sizes are in `backend/app/prompts/` and `MODEL_PROFILE`. Measure with
`scripts/ocr_gateway_eval.py` (ground truth: `Beispielzähler/ground-truth.json`).

## Ollama Vision Model (Optional, Recommended)

Reading extraction is fully optional — the app starts and operates
normally with the LLM path, the OCR service, both, or neither configured.
Ollama is the primary, recommended extraction method; OCR (below) is a
secondary fallback. OCR endpoints return a clear error when a specific
engine is requested but unavailable, rather than crashing.

When `OLLAMA_URL` is configured, the model is asked for a structured JSON response containing the meter reading and serial number:

```
{"reading": "12345.6", "serial": "0012345678"}
```

If Ollama is unavailable or returns nothing, the OCR service (if configured) is used as fallback.

Recommended model: [`gemma4:e4b`](https://ollama.com/library/gemma4) — best accuracy on mechanical and digital meter displays, runs on 6–8 GB VRAM. Uses `think: false` and `temperature: 0` for deterministic output.

```bash
ollama pull gemma4:e4b
```

## OCR Service (Optional)

A standalone, separately-built container (`ocr-service/`) providing
EasyOCR-based extraction with custom preprocessing (CLAHE, unsharp mask,
weighted grayscale) as a fallback to the LLM path. It is not bundled into
the backend image — the backend carries no OCR-engine weight at all when
`OCR_URL` is left empty.

- Enable it by running the `ocr` service in `docker-compose.yaml` (on by
  default in the quickstart) or setting `ocr.enabled: true` in the Helm
  chart's `values.yaml`, and pointing `OCR_URL` at it.
- The OCR service authenticates `/scan` and `/serial` with a shared secret:
  when `OCR_API_KEY` is set it requires `Authorization: Bearer <OCR_API_KEY>`
  (constant-time compare, `/health` stays open for probes) and the backend
  sends the same value. Compose requires `OCR_API_KEY` in `.env`
  (`openssl rand -base64 32`); the Helm chart requires `ocr.existingSecret`
  (Secret key `OCR_API_KEY`) unless `ocr.allowUnauthenticated=true`. Without a
  key the service still runs, unauthenticated, and logs a warning at startup.
- Disable it entirely by not running the container / leaving `OCR_URL`
  empty — the app works the same as with any other optional subsystem
  turned off (see Health Check below).

## Health Check

`GET /api/health` is anonymous and returns only `{"status": "ok"}` (or `"degraded"` with HTTP 503
when the database or the scheduler is down). It does not contact OCR or the LLM, so it is safe as
readiness probe. `GET /api/health/live` is the process-only liveness check.

`GET /api/health/details` (admin login required) returns the subsystem detail; OCR/LLM results are
cached for 30 s:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "db": true,
  "scheduler": true,
  "ocr": true,
  "llm": true,
  "llm_model": "gemma4:e4b"
}
```

- **status** is `"ok"` when DB and scheduler are healthy, `"degraded"` otherwise (HTTP 503).
- **ocr** / **llm** reflect availability but do not affect overall status — they are optional.
- The frontend footer indicator currently reads `/api/health`; anonymous visitors see only ok/degraded, the OCR/LLM lights need `/api/health/details` (admin).

## Helm Chart (Kubernetes)

```bash
helm upgrade --install zaehl-o-mat ./chart \
  --namespace zaehl-o-mat --create-namespace \
  --set backend.existingSecret=zaehl-o-mat-secrets \
  --set ingress.host=zaehlomat.example.com
```

The chart expects a Kubernetes Secret named by `backend.existingSecret` with these keys:

```
DATABASE_URL
JWT_SECRET_KEY
OIDC_CLIENT_ID
OIDC_CLIENT_SECRET
OIDC_DISCOVERY_URL
OIDC_REDIRECT_URI
SUPERADMIN_USER
SUPERADMIN_PASSWORD
```

Every key of that Secret becomes an environment variable of the backend pod
(`envFrom`), so it is visible to anyone who may `kubectl describe` the pod or
read its `/proc`. To avoid that, deliver the secrets as files instead with
`backend.secretVolume.enabled=true` (one key per file under `/run/secrets`; the
app reads both, environment variables win). Keep the database password consistent
between the backend Secret (`DATABASE_URL`) and the PostgreSQL Secret
(`postgresql.auth.existingSecret`).

HSTS is configurable via `ingress.hsts.seconds`, `ingress.hsts.includeSubdomains`
and `ingress.hsts.preload` (defaults: one year, both `true`). Set `preload` and
`includeSubdomains` to `false` when the host shares a parent domain with other
services. The chart and the Compose proxy (`nginx.conf`) send the same CSP and
header set; HSTS is chart-only (TLS terminates at Traefik).

The JWT lives in `sessionStorage` (not `localStorage`, no cookie): it is gone when
the tab closes and is removed on logout and on any 401. This is an accepted
trade-off for an SPA with a strict CSP (`script-src 'self'`, no inline script, no
HTML sinks); moving it to an HttpOnly cookie would need CSRF protection.

## Role-Based Access

| Role | Permissions |
|---|---|
| `superadmin` | Full access, including all properties and built-in admin login |
| `admin` | Full access to all properties; can edit and delete any reading |
| `manager` | Read/write access to assigned properties (add readings, no delete) |
| `user` | Read-only access to assigned properties |

Admins and superadmins see **Edit** (✏️) and **Delete** (🗑️) buttons next to every reading in the meter detail view. Editable fields: meter value, date/time, and optional note.

## Development

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload

# Frontend
cd frontend
npm install
npm run dev
```

The frontend dev server runs on port 5173 and proxies `/api` to the backend on port 8000.

### Pre-commit checks

Local security checks mirroring the CI gates (bandit, gitleaks,
eslint-plugin-security) — catches findings before push instead of only in
CI:

```bash
pip install pre-commit
pre-commit install
```

Requires a Go toolchain on `PATH` (the gitleaks hook builds itself) and
`npm ci` already run under `frontend/`. Run against the whole repo any time
with `pre-commit run --all-files`.

## Contributing

Changes land via pull request against `main` — direct pushes are blocked
by branch protection. All required CI checks (backend tests, bandit,
frontend type-check + eslint-plugin-security, Trivy, gitleaks, image build)
must pass before a PR can merge; see the [project constitution](.specify/memory/constitution.md)
for the full rationale and requirements.

## License

MIT
