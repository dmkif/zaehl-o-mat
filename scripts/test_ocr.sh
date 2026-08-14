#!/bin/bash
set -e

TOKEN=$(curl -s -X POST 'http://localhost:8080/api/auth/superadmin-login' \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' | \
  python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")

echo "Token length: ${#TOKEN}"

for img in /home/dmkif/zaehl-o-mat/Beispielzähler/*.jpg; do
  echo ""
  echo "=== $(basename $img) ==="
  result=$(curl -s -X POST 'http://localhost:8080/api/ocr/scan' \
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
