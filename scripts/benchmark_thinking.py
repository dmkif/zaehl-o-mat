#!/usr/bin/env python3
"""
Benchmark: think=False vs think=True für OCR-Qualität und Laufzeit.
Testet alle Bilder in Beispielzähler/ gegen die laufende Ollama-Instanz.

Aufruf:
    python benchmark_thinking.py [--url http://localhost:11434] [--model gemma4:e4b]
"""

import argparse
import base64
import io
import json
import re
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

# ---------------------------------------------------------------------------
# Prompt (identisch mit _llm_fallback in ocr.py)
# ---------------------------------------------------------------------------
PROMPT = (
    "Read the utility meter shown in the image above.\n"
    "\n"
    "READING — the main consumption counter:\n"
    "- Read ALL digit positions left to right, including leading zeros.\n"
    "- Decimal separator rules (exactly one decimal separator is possible):\n"
    "  • If you see BOTH a period (.) AND a comma (,): the period is a"
    " thousands separator — ignore it. The comma is the decimal separator"
    " — write it as a period (.) in your answer.\n"
    "  • If you see only a period (.): it is the decimal separator —"
    " keep it as a period (.) in your answer.\n"
    "  • If you see only a comma (,): it is the decimal separator —"
    " write it as a period (.) in your answer.\n"
    "  • If you see no separator at all: return only the integer digits"
    " (e.g. '1374', NOT '1374.' or '1374.0').\n"
    "- Do NOT skip any drums or windows, even if dim or partially rotated.\n"
    "- Ignore handwritten numbers, stickers, adhesive labels, or annotations.\n"
    "- No spaces, no units (kWh/m³/…).\n"
    "\n"
    "SERIAL — the device identifier on the meter label:\n"
    "- Look for a label starting with Nr., S/N, Zähler-Nr., MSN, or similar.\n"
    "- Copy only the characters belonging to THAT one label — do not merge"
    " multiple labels.\n"
    "- Include ALL leading zeros exactly as printed.\n"
    "- Only alphanumeric characters (A–Z, 0–9) — strip ALL spaces and"
    " punctuation.\n"
    "- If no serial label is visible, use null.\n"
    "\n"
    "If a field is unreadable, use null."
)

FORMAT_SCHEMA = {
    "type": "object",
    "properties": {
        "reading": {"type": ["string", "null"]},
        "serial": {"type": ["string", "null"]},
    },
    "required": ["reading", "serial"],
}

OPTIONS = {"temperature": 0, "num_ctx": 8192, "num_image_tokens": 1120}
MAX_SIDE = 1500  # unkroppte Fotos


def prepare_image(path: Path) -> str:
    img = Image.open(path)
    if max(img.size) > MAX_SIDE:
        scale = MAX_SIDE / max(img.size)
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def call_ollama(url: str, model: str, b64: str, think: bool) -> dict:
    payload = {
        "model": model,
        "prompt": PROMPT,
        "images": [b64],
        "stream": False,
        "format": FORMAT_SCHEMA,
        "think": think,
        "options": OPTIONS,
    }
    t0 = time.perf_counter()
    resp = httpx.post(f"{url}/api/generate", json=payload, timeout=300.0)
    elapsed = time.perf_counter() - t0
    resp.raise_for_status()
    body = resp.json()
    raw = body.get("response", "").strip()
    thinking_text = body.get("thinking", "")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{[^}]+\}", raw, re.DOTALL)
        try:
            data = json.loads(m.group()) if m else {}
        except json.JSONDecodeError:
            data = {}

    return {
        "reading": data.get("reading"),
        "serial": data.get("serial"),
        "elapsed": elapsed,
        "thinking_chars": len(thinking_text),
        "raw": raw,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Think vs No-Think OCR benchmark")
    parser.add_argument("--url", default="http://localhost:11434", help="Ollama base URL")
    parser.add_argument("--model", default="gemma4:e4b", help="Ollama model tag")
    parser.add_argument(
        "--images-dir",
        default=str(Path(__file__).parent / "Beispielzähler"),
        help="Verzeichnis mit Beispielbildern",
    )
    args = parser.parse_args()

    images_dir = Path(args.images_dir)
    images = sorted(images_dir.glob("*.jpg")) + sorted(images_dir.glob("*.jpeg")) + sorted(images_dir.glob("*.png"))

    if not images:
        print(f"Keine Bilder in {images_dir} gefunden.", file=sys.stderr)
        sys.exit(1)

    print(f"Modell : {args.model}")
    print(f"Ollama : {args.url}")
    print(f"Bilder : {len(images)}")
    print("=" * 80)

    totals = {"no_think": 0.0, "think": 0.0}
    rows = []

    for img_path in images:
        print(f"\n📷  {img_path.name}")
        b64 = prepare_image(img_path)

        # --- think=False ---
        r_no = call_ollama(args.url, args.model, b64, think=False)
        print(f"  think=False  {r_no['elapsed']:6.1f}s  reading={r_no['reading']!r:20}  serial={r_no['serial']!r}")

        # --- think=True ---
        r_yes = call_ollama(args.url, args.model, b64, think=True)
        print(f"  think=True   {r_yes['elapsed']:6.1f}s  reading={r_yes['reading']!r:20}  serial={r_yes['serial']!r}  (thinking: {r_yes['thinking_chars']} chars)")

        # Übereinstimmung?
        match = "✅ gleich" if r_no["reading"] == r_yes["reading"] else "⚠️  verschieden"
        print(f"  → {match}  |  Overhead: +{r_yes['elapsed'] - r_no['elapsed']:.1f}s")

        totals["no_think"] += r_no["elapsed"]
        totals["think"] += r_yes["elapsed"]
        rows.append(
            {
                "file": img_path.name,
                "no_think_reading": r_no["reading"],
                "think_reading": r_yes["reading"],
                "no_think_s": round(r_no["elapsed"], 2),
                "think_s": round(r_yes["elapsed"], 2),
                "same": r_no["reading"] == r_yes["reading"],
                "thinking_chars": r_yes["thinking_chars"],
            }
        )

    # --- Zusammenfassung ---
    print("\n" + "=" * 80)
    print("ZUSAMMENFASSUNG")
    print("=" * 80)
    same_count = sum(1 for r in rows if r["same"])
    diff_count = len(rows) - same_count
    print(f"  Bilder gesamt      : {len(rows)}")
    print(f"  Gleiche Ergebnisse : {same_count}/{len(rows)}")
    print(f"  Verschiedene       : {diff_count}/{len(rows)}")
    print(f"  Gesamtzeit think=False: {totals['no_think']:.1f}s")
    print(f"  Gesamtzeit think=True : {totals['think']:.1f}s")
    print(f"  Ø Overhead pro Bild   : {(totals['think'] - totals['no_think']) / len(rows):.1f}s")

    if diff_count:
        print("\nBilder mit abweichendem Ergebnis:")
        for r in rows:
            if not r["same"]:
                print(f"  {r['file']}: {r['no_think_reading']!r} → {r['think_reading']!r}")

    # JSON-Dump für weitere Auswertung
    out = Path(__file__).parent / "benchmark_results.json"
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    print(f"\n💾  Detailergebnisse: {out}")


if __name__ == "__main__":
    main()
