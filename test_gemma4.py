#!/usr/bin/env python3
"""Quick test: send a meter image to gemma4:e2b via Ollama API."""
import base64, json, sys
from pathlib import Path
import urllib.request

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "gemma4:e4b"
PROMPT = """\
Analyze the utility meter in this photo.

Return ONLY valid JSON: {"reading": "VALUE", "serial": "VALUE"}

READING:
- The main consumption counter on the digital display.
- Read ALL digit positions from left to right, including leading zeros.
- Include the decimal point if present.
- Do NOT skip any drums or windows, even if dim or partially rotated.
- Ignore handwritten numbers, stickers, or annotations.
- No spaces, no units, no thousands separators.

SERIAL:
- The serial number or device ID printed on the meter label.
- Look for labels like Nr., S/N, Zähler-Nr., Eigentum, or similar.
- Include ALL leading zeros exactly as printed.
- Only alphanumeric characters, no spaces or punctuation.

If a field is unreadable, use null.\
"""

# Find test images
img_dir = Path("/home/dmkif/zaehl-o-mat/Beispielzähler")
images = sorted(img_dir.glob("*.jpg"))

if not images:
    print("No images found in", img_dir)
    sys.exit(1)

for img_path in images:
    print(f"\n{'='*60}")
    print(f"Image: {img_path.name}")
    b64 = base64.b64encode(img_path.read_bytes()).decode()

    payload = json.dumps({
        "model": MODEL,
        "prompt": PROMPT,
        "images": [b64],
        "stream": False,
        "format": "json",
        "think": False,
        "options": {
            "temperature": 0,
            "num_ctx": 8192,
        },
    }).encode()

    req = urllib.request.Request(OLLAMA_URL, data=payload,
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read())
            raw = result.get('response', '').strip()
            thinking = result.get('thinking', '').strip()
            total_dur = result.get("total_duration", 0)
            if thinking:
                print(f"Thinking: {thinking[:300]}{'...' if len(thinking) > 300 else ''}")
            print(f"Raw response: {raw}")
            try:
                parsed = json.loads(raw)
                print(f"  reading: {parsed.get('reading')}")
                print(f"  serial:  {parsed.get('serial')}")
            except json.JSONDecodeError:
                print("  (could not parse JSON)")
            print(f"Duration: {total_dur / 1e9:.1f}s")
            sys.stdout.flush()
    except Exception as e:
        print(f"Error: {e}")
        sys.stdout.flush()
