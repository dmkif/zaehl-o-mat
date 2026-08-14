#!/usr/bin/env python3
"""Diagnostic: ask gemma4 why it returned wrong values for a specific meter image."""
import base64, json
from pathlib import Path
import urllib.request

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "gemma4:e2b"
IMAGE = Path("/home/dmkif/zaehl-o-mat/Beispielzähler/IMG_20260404_232126.jpg")

PROMPT_DIAGNOSE = (
    "You are analyzing a photo of a utility meter.\n"
    "A previous OCR attempt returned: reading='1009.9', serial='23456789'.\n"
    "The CORRECT values are: reading='31009.9', serial='00026991'.\n\n"
    "Please answer the following questions in detail:\n"
    "1. Where exactly on the meter display is the digit '3' located that belongs to the reading?\n"
    "   Why might it be easy to miss or confuse it?\n"
    "2. What does the full meter display look like? Describe each digit position you can see.\n"
    "3. Where is the serial number '00026991' located on the meter label?\n"
    "   What label or prefix does it appear next to (e.g. Nr., S/N, Zähler-Nr.)?\n"
    "   Why might '23456789' have been returned instead?\n"
    "4. Are there multiple numbers visible on the meter? List ALL numbers you can see and\n"
    "   explain which one is the serial number vs. other codes or identifiers.\n"
    "5. What visual characteristics of this meter make it difficult to read correctly?\n\n"
    "Be as specific and detailed as possible. Describe the exact visual layout."
)

b64 = base64.b64encode(IMAGE.read_bytes()).decode()

print(f"Sending diagnostic query for: {IMAGE.name}")
print("=" * 70)

payload = json.dumps({
    "model": MODEL,
    "prompt": PROMPT_DIAGNOSE,
    "images": [b64],
    "stream": False,
    "think": True,
    "num_ctx": 8192,
    "options": {
        "temperature": 0,
    },
}).encode()

req = urllib.request.Request(OLLAMA_URL, data=payload,
                              headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=300) as resp:
        result = json.loads(resp.read())
        thinking = result.get('thinking', '').strip()
        response = result.get('response', '').strip()
        total_dur = result.get("total_duration", 0)

        if thinking:
            print("\n--- THINKING (chain-of-thought) ---")
            print(thinking)

        print("\n--- MODEL EXPLANATION ---")
        print(response)
        print(f"\nDuration: {total_dur / 1e9:.1f}s")
except Exception as e:
    print(f"Error: {e}")
