#!/usr/bin/env python3
"""
Messlauf der Zählerablesung gegen das KI-Gateway (Spec 012, SC-001/SC-002/SC-007).

Liest die Beispielzähler (Beispielzähler/ground-truth.json) mehrfach über denselben
Code wie das Backend (ocr_pipeline._llm_fallback) und wertet Zählerstand und
Seriennummer gegen die Ground Truth aus — getrennt nach antwortendem Modell.

  GATEWAY_BASE_URL=https://ai.apps.schmulzer.de/v1 GATEWAY_API_KEY=... \\
      python scripts/ocr_gateway_eval.py --runs 3 [--model-profile qwen3.5-9b]

Der Key wird nur aus der Umgebung gelesen und nie ausgegeben. Danach im Gateway
prüfen, dass OpenRouter-Aktivität 0 ist.
"""
import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

# Backend settings validate DB/JWT secrets at import; this script touches neither.
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("JWT_SECRET_KEY", "eval-only-not-a-real-secret")

from app.services import llm_client, ocr_pipeline  # noqa: E402


def norm_serial(s: str | None) -> str | None:
    return None if not s else "".join(ch for ch in s.upper() if ch.isalnum())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--dir", type=Path, default=ROOT / "Beispielzähler")
    ap.add_argument("--model-profile", default="", help="Modellprofil (Standard: qwen3.5-9b)")
    ap.add_argument("--meter-type", action="store_true",
                    help="Zählertyp mitgeben wie die Pipeline nach dem Seriennummern-Abgleich "
                         "(deterministische Separator-Korrektur, ohne Last-Reading-Hint im Prompt)")
    ap.add_argument("--json", type=Path, help="Rohergebnisse als JSON speichern (ohne Key)")
    args = ap.parse_args()

    if not os.environ.get("GATEWAY_BASE_URL") or not os.environ.get("GATEWAY_API_KEY"):
        print("GATEWAY_BASE_URL und GATEWAY_API_KEY müssen gesetzt sein.", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.WARNING)

    from pydantic import SecretStr

    s = llm_client.settings
    s.ocr_backend = "gateway"
    s.gateway_base_url = os.environ["GATEWAY_BASE_URL"]
    s.gateway_api_key = SecretStr(os.environ["GATEWAY_API_KEY"])
    s.gateway_profile = os.environ.get("GATEWAY_PROFILE", "local-only")
    s.gateway_timeout_s = float(os.environ.get("GATEWAY_TIMEOUT_S", "300"))
    s.model_profile = args.model_profile

    truth = json.loads((args.dir / "ground-truth.json").read_text(encoding="utf-8"))
    truth = {k: v for k, v in truth.items() if not k.startswith("_")}

    # Das antwortende Modell steht nur im Log von llm_client; hier abgreifen.
    answering: list[str | None] = []
    orig = llm_client.gateway_complete

    def spy(b64, prompt, profile):
        text, model = orig(b64, prompt, profile)
        answering.append(model)
        return text, model

    llm_client.gateway_complete = spy

    stats = defaultdict(lambda: {"n": 0, "reading": 0, "serial": 0})
    rows = []
    for name, gt in truth.items():
        path = args.dir / name
        for run in range(1, args.runs + 1):
            answering.clear()
            try:
                reading, serial = ocr_pipeline._llm_fallback(
                    path,
                    meter_type=gt["meter_type"] if args.meter_type else None,
                )
                err = None
            except llm_client.LLMError as exc:
                reading = serial = None
                err = type(exc).__name__
            model = answering[0] if answering else "(keine Antwort)"
            r_ok = reading == gt["reading"]
            s_ok = norm_serial(serial) == norm_serial(gt["serial"])
            st = stats[model]
            st["n"] += 1
            st["reading"] += r_ok
            st["serial"] += s_ok
            rows.append({"image": name, "run": run, "model": model, "reading": reading, "reading_ok": r_ok,
                         "serial": serial, "serial_ok": s_ok, "error": err})
            print(f"{name} #{run} [{model}] stand={'OK ' if r_ok else 'FEHL'} {reading!r:>14} "
                  f"serial={'OK ' if s_ok else 'FEHL'} {serial!r}{' ' + err if err else ''}")

    total = sum(v["n"] for v in stats.values())
    print("\n== Ergebnis je antwortendem Modell ==")
    for model, v in stats.items():
        print(f"{model}: Stand {v['reading']}/{v['n']}, Seriennummer {v['serial']}/{v['n']}")
    print(f"Gesamt: Stand {sum(v['reading'] for v in stats.values())}/{total}  (Ziel ≥ 15/18 je Stufe, Referenz gemma4:e4b 15/18)")
    if args.json:
        args.json.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
