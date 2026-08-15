# Zähl-O-Mat

A self-hosted utility meter management application. Take a photo of any meter (electricity, water, gas, oil), and the app extracts the reading via OCR or a local Ollama vision model. Readings are stored per property, visualized in charts, and the oil heating price is fetched daily for cost calculations.

## Features

- Multi-tenant: properties → meters → readings hierarchy
- OCR pipeline: EasyOCR + optional Ollama vision model (GPU-accelerated)
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

The app is available at **http://localhost:8080**.  
Default superadmin credentials: `admin` / `admin123` (change via env vars).

> **Note:** The stack includes an Ollama container that expects an NVIDIA GPU.  
> Without a GPU, remove the `devices:` block from `docker-compose.yaml` and set  
> `OLLAMA_MODEL` to a small CPU-capable model, or leave `OLLAMA_URL` empty to use  
> EasyOCR only.

## Environment Variables

All variables are set on the `backend` service.

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://...` | PostgreSQL connection string |
| `JWT_SECRET_KEY` | *(required)* | Secret for signing JWT tokens — change in production |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Token lifetime in minutes |
| `SUPERADMIN_USER` | `""` | Username for the built-in superadmin account |
| `SUPERADMIN_PASSWORD` | `""` | Password for the built-in superadmin account |
| `UPLOAD_PATH` | `/data/uploads` | Directory where meter images are stored |
| `APP_BASE_URL` | `https://zaehl-o-mat.example.com` | Public base URL (used for CORS + OIDC) |
| `DEBUG` | `false` | Enable debug logging |
| **Ollama** | | |
| `OLLAMA_URL` | `""` | Ollama API base URL (e.g. `http://ollama:11434`). Leave empty to disable. |
| `OLLAMA_MODEL` | `gemma4:e4b` | Vision model name |
| `OLLAMA_API_KEY` | `""` | Bearer token for hosted/proxied Ollama endpoints. Not needed for a local, unauthenticated Ollama. |
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
2. Set the redirect URI to `https://<your-domain>/auth/callback` (hash-based routing: `/#/auth/token` is handled by the frontend).
3. Copy the client ID, secret and discovery URL into the env vars above.
4. Create groups matching `OIDC_ADMIN_GROUP` / `OIDC_MANAGER_GROUP` / `OIDC_USER_GROUP` and assign users.

When `OIDC_CLIENT_ID` is set, the login page shows a **"Login with SSO"** button in addition to the local superadmin login.

## Ollama Vision Model (Optional)

The OCR pipeline is fully optional.  If `OLLAMA_URL` is empty the LLM path is
skipped; if `easyocr` is not installed the classical OCR path is skipped.  The
app starts and operates normally without either — OCR endpoints will return
appropriate errors when a specific engine is requested but unavailable.

When `OLLAMA_URL` is configured, the model is asked for a structured JSON response containing the meter reading and serial number:

```
{"reading": "12345.6", "serial": "0012345678"}
```

If Ollama is unavailable or returns nothing, EasyOCR with custom preprocessing (CLAHE, unsharp mask, weighted grayscale) is used as fallback.

Recommended model: [`gemma4:e4b`](https://ollama.com/library/gemma4) — best accuracy on mechanical and digital meter displays, runs on 6–8 GB VRAM. Uses `think: false` and `temperature: 0` for deterministic output.

```bash
ollama pull gemma4:e4b
```

## Health Check

`GET /api/health` returns the status of all subsystems:

```json
{
  "status": "ok",
  "db": true,
  "scheduler": true,
  "ocr": true,
  "llm": true,
  "llm_model": "gemma4:e4b"
}
```

- **status** is `"ok"` when DB and scheduler are healthy, `"degraded"` otherwise (HTTP 503).
- **ocr** / **llm** reflect availability but do not affect overall status — they are optional.
- The frontend shows these as a traffic-light indicator in the footer.

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

See [`chart/values.yaml`](chart/values.yaml) for all available values.

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

## License

MIT
