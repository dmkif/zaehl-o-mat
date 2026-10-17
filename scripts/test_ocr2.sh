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
  python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null)

echo "Token length: ${#TOKEN}"
if [ ${#TOKEN} -lt 10 ]; then
  echo "Auth failed!"
  exit 1
fi

PHOTOS=(
  "$PHOTO_DIR/IMG_20260402_123222.jpg:Holley_electricity_expect_0309735"
  "$PHOTO_DIR/IMG_20260402_123236.jpg:Proteus_oil_expect_1374"
  "$PHOTO_DIR/IMG_20260402_123256.jpg:DRUS_water_expect_165539"
)

for entry in "${PHOTOS[@]}"; do
  IMG="${entry%%:*}"
  LABEL="${entry##*:}"
  echo ""
  echo "=== $LABEL ==="
  curl -s -X POST "$BASE_URL/api/ocr/scan" \
    -H "Authorization: Bearer $TOKEN" \
    -F "file=@${IMG}" \
    -F "meter_id=1" > /tmp/ocr_single.json 2>/dev/null

  python3 - << 'PYEOF'
import sys, json
with open('/tmp/ocr_single.json') as f:
    content = f.read()
if not content.strip():
    print("  EMPTY RESPONSE")
else:
    try:
        d = json.loads(content)
        print(f"  Detected: {d.get('detected_value')}")
        for item in d.get('raw_texts', []):
            if isinstance(item, (list, tuple)) and len(item) == 3:
                bbox, text, conf = item
                print(f"    text={repr(text)}  conf={round(float(conf),3)}")
            else:
                print(f"    item={repr(item)}")
        if 'detail' in d:
            print(f"  ERROR: {d['detail']}")
    except json.JSONDecodeError as e:
        print(f"  JSON error: {e}")
        print(f"  Raw: {content[:200]}")
PYEOF
done
