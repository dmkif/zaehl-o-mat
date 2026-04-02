#!/usr/bin/env python3
"""
Quick standalone OCR test — runs outside container, uses local pillow + easyocr.
Saves preprocessed images so we can inspect them.
"""
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageOps, ImageFilter
    import easyocr
except ImportError as e:
    print(f"Missing dependency: {e}")
    sys.exit(1)

PHOTOS = sorted(Path("/home/dmkif/zaehl-o-mat/Beispielzähler").glob("*.jpg"))
EXPECTED = {
    "IMG_20260402_123222.jpg": "0309735",   # Holley electricity
    "IMG_20260402_123236.jpg": "1374",      # Proteus oil
    "IMG_20260402_123256.jpg": "165539",    # DRUS water
}


def preprocess(filepath: Path):
    img = Image.open(filepath)
    max_dim = 2000
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    orig_size = img.size

    if img.mode in ("RGB", "RGBA"):
        rgb = img.convert("RGB")
        r_arr = np.array(rgb.split()[0], dtype=np.float32)
        g_arr = np.array(rgb.split()[1], dtype=np.float32)
        b_arr = np.array(rgb.split()[2], dtype=np.float32)
        gray = (0.10 * r_arr + 0.70 * g_arr + 0.20 * b_arr).clip(0, 255).astype(np.uint8)
        img = Image.fromarray(gray, mode="L")
    else:
        img = img.convert("L")

    img = ImageOps.autocontrast(img, cutoff=1)
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
    return img, orig_size


def preprocess_standard(filepath: Path):
    """Standard grayscale (no red suppression) for comparison."""
    img = Image.open(filepath)
    max_dim = 2000
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    orig_size = img.size
    img = img.convert("L")
    img = ImageOps.autocontrast(img, cutoff=1)
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
    return img, orig_size


def preprocess_max_channel(filepath: Path):
    """Take max(R,G,B) as intensity — captures any bright LED regardless of color."""
    img = Image.open(filepath)
    max_dim = 2000
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    orig_size = img.size
    if img.mode in ("RGB", "RGBA"):
        rgb = img.convert("RGB")
        r, g, b = rgb.split()
        gray = Image.fromarray(
            np.maximum(np.maximum(np.array(r), np.array(g)), np.array(b)), "L"
        )
    else:
        gray = img.convert("L")
    gray = ImageOps.autocontrast(gray, cutoff=1)
    gray = gray.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
    return gray, orig_size


print("Initializing EasyOCR (first run downloads model weights) ...")
reader = easyocr.Reader(["de", "en"], gpu=False)
print("Done.\n")

OUT_DIR = Path("/tmp/ocr_debug")
OUT_DIR.mkdir(exist_ok=True)

for photo in PHOTOS:
    name = photo.name
    expected = EXPECTED.get(name, "?")
    print(f"{'='*60}")
    print(f"Photo: {name}  (expected: {expected})")

    for mode, fn in [("weighted_gray", preprocess), ("std_gray", preprocess_standard), ("max_ch", preprocess_max_channel)]:
        proc_img, orig_size = fn(photo)
        out_path = OUT_DIR / f"{photo.stem}_{mode}.jpg"
        proc_img.save(out_path, quality=95)

        results = reader.readtext(str(out_path), detail=1, allowlist="0123456789.,")
        texts = [(text, round(conf, 3)) for (_, text, conf) in results]
        # Find best candidate (simple: highest conf × digit bonus)
        import re
        best = None
        best_score = -1
        for bbox, text, conf in results:
            digits = re.sub(r"\D", "", text.replace(",", "."))
            if len(digits) < 3:
                continue
            score = conf + (0.3 if 4 <= len(digits) <= 8 else -0.4 if len(digits) > 8 else 0)
            if score > best_score:
                best_score = score
                best = text
        print(f"  [{mode}] texts={texts}  best={best}  saved→{out_path.name}")

print("\nDone. Preprocessed images in:", OUT_DIR)
