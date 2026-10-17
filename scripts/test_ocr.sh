#!/bin/bash
# Dev tooling (F-043): reads the target and credentials from the environment.
#   BASE_URL (default http://localhost:8081, the Compose proxy), SUPERADMIN_USER,
#   SUPERADMIN_PASSWORD (your .env values), PHOTO_DIR (directory with *.jpg meter photos).
set -euo pipefail
: "${SUPERADMIN_USER:?set SUPERADMIN_USER (see .env)}"
: "${SUPERADMIN_PASSWORD:?set SUPERADMIN_PASSWORD (see .env)}"
: "${PHOTO_DIR:?set PHOTO_DIR to a directory with meter photos}"
BASE_URL="${BASE_URL:-http://localhost:8081}"
LOGIN_BODY=$(python3 -c 'import json,os; print(json.dumps({"username": os.environ["SUPERADMIN_USER"], "password": os.environ["SUPERADMIN_PASSWORD"]}))')

TOKEN=$(curl -s -X POST "$BASE_URL/api/auth/superadmin-login" \
  -H 'Content-Type: application/json' \
  -d "$LOGIN_BODY" | \
  python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")

echo "Token length: ${#TOKEN}"

for img in "$PHOTO_DIR"/*.jpg; do
  echo ""
  echo "=== $(basename "$img") ==="
  result=$(curl -s -X POST "$BASE_URL/api/ocr/scan" \
    -H "Authorization: Bearer $TOKEN" \
    -F "file=@$img" \
    -F "meter_id=1")
  echo "$result" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('Detected:', d.get('detected_value'))
for item in d.get('raw_texts', []):
    if isinstance(item, list) and len(item) == 3:
        print(f'  text={item[1]}  conf={round(float(item[2]),3)}')
    else:
        print('  raw_item:', item)
if 'detail' in d:
    print('ERROR:', d['detail'])
"
done
