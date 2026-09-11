# Quickstart: Validate OCR as an Optional External Service

Run these scenarios after implementation to confirm the feature end-to-end.
See `contracts/ocr-service-api.md` for the exact request/response shapes and
`data-model.md` for the configuration keys referenced below.

## 1. Backend runs correctly with OCR fully absent (US1 / FR-001, FR-003)

```bash
# Build only backend + frontend + proxy + db + ollama — no ocr-service at all
docker compose up -d db ollama backend frontend proxy

curl -s http://localhost:8081/api/health | jq
# Expect: "ocr": false, overall "status": "ok" (assuming db/scheduler healthy)

# Upload a meter photo via the UI or curl; confirm a reading comes back via the LLM path
curl -s -F "file=@sample-meter.jpg" -F "engine=llm" \
  http://localhost:8081/api/ocr/scan | jq '.detection_method'
# Expect: "llm"
```

Confirm the backend image itself carries no OCR weight:

```bash
docker build -t zaehl-o-mat-backend:test ./backend
docker run --rm zaehl-o-mat-backend:test python -c "import easyocr" 2>&1 | tail -1
# Expect: ModuleNotFoundError (easyocr is not installed in the backend image)
```

## 2. Enable the optional OCR service and confirm the fallback works (US2 / FR-002, FR-004)

```bash
docker compose up -d ocr   # the new optional service
# backend env: OCR_URL=http://ocr:8100 (set in docker-compose.yaml per this feature)
docker compose restart backend

curl -s http://localhost:8081/api/health | jq '.ocr'
# Expect: true

curl -s -F "file=@sample-meter.jpg" -F "engine=ocr" \
  http://localhost:8081/api/ocr/scan | jq
# Expect: detection_method "ocr", detected_value populated, response shape
# identical to pre-change behavior (see data-model.md "OCR Scan Result")
```

## 3. OCR service unreachable — graceful degradation (US2 edge case / FR-005)

```bash
docker compose stop ocr

curl -s http://localhost:8081/api/health | jq '.ocr, .status'
# Expect: false, "ok"  (overall status unaffected — Constitution Principle II)

curl -s -F "file=@sample-meter.jpg" -F "engine=ocr" \
  http://localhost:8081/api/ocr/scan
# Expect: a clean error response (503-class), not a hang or a 500 crash trace
```

## 4. Helm: OCR disabled by default (US3 / FR-008, SC-005)

```bash
helm template zaehl-o-mat ./chart | grep -c "kind: Deployment"
# Expect: 2 (backend, frontend) — no ocr Deployment rendered

helm install zaehl-o-mat ./chart --namespace zaehl-o-mat --create-namespace \
  --set backend.existingSecret=zaehl-o-mat-secrets
# Expect: successful install, backend reports ocr:false via /api/health
```

## 5. Helm: OCR enabled (US3 / FR-008, SC-005)

```bash
helm template zaehl-o-mat ./chart --set ocr.enabled=true | grep -c "kind: Deployment"
# Expect: 3 (backend, frontend, ocr)

helm upgrade zaehl-o-mat ./chart --namespace zaehl-o-mat --set ocr.enabled=true
kubectl -n zaehl-o-mat rollout status deploy/zaehl-o-mat-ocr
# Expect: rollout succeeds; backend pod's OCR_URL env now points at the
# in-cluster ocr Service; /api/health reports ocr:true
```

## 6. Regression check — extraction accuracy unchanged (SC-004)

Run the relocated `ocr-service/tests/test_scoring.py` (formerly
`backend/tests/test_ocr_scoring.py`) against the same fixture images used
before this change; all existing assertions must still pass unmodified,
since the scoring logic itself is moved, not rewritten.

```bash
cd ocr-service && python -m pytest tests/test_scoring.py -v
```
