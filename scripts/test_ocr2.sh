#!/bin/bash
# Test OCR endpoint with the 3 meter photos

TOKEN=$(curl -s -X POST 'http://localhost:8080/api/auth/superadmin-login' \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' | \
  python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null)

echo "Token length: ${#TOKEN}"
if [ ${#TOKEN} -lt 10 ]; then
  echo "Auth failed!"
  exit 1
fi

PHOTOS=(
  "/home/dmkif/zaehl-o-mat/Beispielzähler/IMG_20260402_123222.jpg:Holley_electricity_expect_0309735"
  "/home/dmkif/zaehl-o-mat/Beispielzähler/IMG_20260402_123236.jpg:Proteus_oil_expect_1374"
  "/home/dmkif/zaehl-o-mat/Beispielzähler/IMG_20260402_123256.jpg:DRUS_water_expect_165539"
)

for entry in "${PHOTOS[@]}"; do
  IMG="${entry%%:*}"
  LABEL="${entry##*:}"
  echo ""
  echo "=== $LABEL ==="
  curl -s -X POST 'http://localhost:8080/api/ocr/scan' \
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
