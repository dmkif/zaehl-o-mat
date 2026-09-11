"""
EasyOCR meter-reading extraction pipeline.

Moved verbatim from backend/app/services/ocr_pipeline.py as part of the
OCR-optional-container feature (specs/001-ocr-optional-container/) — this
is a relocation, not a rewrite (FR-010). HTTP orchestration lives in
app.main.
"""
import asyncio
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# EasyOCR reader is lazily initialized to avoid slow startup.
# A lock ensures only one thread initialises or uses the reader at a time.
_reader = None
_reader_lock = asyncio.Lock()


def _get_reader():
    global _reader
    if _reader is None:
        import easyocr  # type: ignore
        _reader = easyocr.Reader(["de", "en"], gpu=True)
    return _reader


def _preprocess_image(filepath: Path, already_cropped: bool = False) -> tuple:
    """
    Pre-process a utility-meter photo for EasyOCR.  Handles three common
    meter display types found in German households:

    - Red-LED backlit LCD (Holley DTS541 etc.): bright red background, dark segments
    - Natural-light reflective LCD (PROTEUS, ITron): bright gray background, dark digits
    - Dark-background LCD (HYDRUS/Diehl, Kamstrup): dark background, light digits

    Pipeline:
    1. Auto-detect the display strip using Canny edges on a blurred grayscale.
       Sigma ≈ 1.5 % of image height suppresses fine label text while preserving
       large (~5 % image height) LCD digit edges.
    2. Extract the red channel from the detected crop (maximises contrast for
       red-LED meters; for others it is an approximate grayscale and still works).
    3. Apply CLAHE (tight 3×3 tiles) to enhance local contrast.
    4. If the result is predominantly dark (dark-background display), invert it
       so that EasyOCR always receives dark text on a bright background.
    5. Denoise + 4× upscale + light sharpen.

    Returns (proc_path, (proc_width, proc_height)).
    """
    import cv2
    import numpy as np
    from PIL import Image, ImageOps

    # cv2.imread ignores EXIF Orientation — load via PIL with transpose so
    # rotated phone photos reach the OCR pipeline upright.
    try:
        _pil = ImageOps.exif_transpose(Image.open(filepath)).convert("RGB")
        img = cv2.cvtColor(np.array(_pil), cv2.COLOR_RGB2BGR)
    except Exception:
        img = cv2.imread(str(filepath))
    if img is None:
        raise ValueError(f"Cannot read image: {filepath}")

    h, w = img.shape[:2]

    # ── Step 1: Auto-detect display strip ───────────────────────────────────
    # Skipped when already_cropped=True: the user already selected the meter
    # display area with the crop UI, so we use the full image as-is.
    if already_cropped:
        display = img
        dh, dw = display.shape[:2]
    else:
        # Blur sigma ≈ 1.5 % of image height: large enough to erase fine print and
        # barcodes (< 0.5 % feature size) but small enough to keep LCD digit edges
        # (typically ~5 % of image height).
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        sigma = max(5.0, h * 0.015)
        blurred_detect = cv2.GaussianBlur(gray, (0, 0), sigma)
        edges = cv2.Canny(blurred_detect, 30, 100)

        strip_h = max(20, h // 20)   # ≈ 5 % of height per scanning strip
        stride  = max(10, h // 50)   # ≈ 2 % stride (50 % overlap between strips)
        best_score = -1.0
        best_y1 = int(h * 0.35)      # safe default

        # Search only in 10–70 % of image height (display is never at top/bottom)
        for y0 in range(int(h * 0.10), int(h * 0.70), stride):
            band = edges[y0:min(h, y0 + strip_h), int(w * 0.10):int(w * 0.90)]
            if band.size == 0:
                continue
            score = float(np.mean(band))  # mean edge density after de-noising blur
            if score > best_score:
                best_score, best_y1 = score, y0

        # Expand winning strip by ±1 strip to avoid clipping digit ascenders/descenders
        crop_y1 = max(0, best_y1 - strip_h)
        crop_y2 = min(h, best_y1 + 2 * strip_h)
        crop_x1 = int(w * 0.10)
        crop_x2 = int(w * 0.90)
        display = img[crop_y1:crop_y2, crop_x1:crop_x2]
        dh, dw = display.shape[:2]
        if dh < 10 or dw < 20:
            display = img   # fallback: use full image
            dh, dw = display.shape[:2]

    # ── Step 2: Red-channel CLAHE ────────────────────────────────────────────
    r_ch = display[:, :, 2]  # OpenCV BGR → index 2 = red
    clahe = cv2.createCLAHE(clipLimit=10.0, tileGridSize=(3, 3))
    enhanced = clahe.apply(r_ch)

    # ── Step 3: Invert dark-background displays ──────────────────────────────
    # HYDRUS / Diehl-type meters show bright white digits on a dark LCD.
    # After red-channel CLAHE those pixels are bright on dark (inverted for OCR).
    # Threshold: overall mean < 90 ≈ predominantly dark crop → invert.
    if float(np.mean(enhanced)) < 90:
        enhanced = cv2.bitwise_not(enhanced)

    # ── Step 4: Denoise + 4× upscale + light sharpen ────────────────────────
    denoised = cv2.fastNlMeansDenoising(enhanced, h=12)
    scale = 4
    upscaled = cv2.resize(denoised, (dw * scale, dh * scale), interpolation=cv2.INTER_CUBIC)
    k = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    sharp = cv2.filter2D(upscaled, -1, k)

    proc_path = filepath.with_suffix(".proc.jpg")
    Image.fromarray(sharp).save(proc_path, quality=95)
    proc_size = (sharp.shape[1], sharp.shape[0])  # (width, height) — PIL convention
    return proc_path, proc_size


# Transliteration table for common 7-segment LCD misreads.
# These arise because EasyOCR was not trained on 7-segment fonts.
_SEG7_SUBS = str.maketrans("JODIlBGT", "00011867")


def _fix_seven_segment(text: str) -> str:
    """
    Apply 7-segment LCD character substitutions and remove intra-digit spaces.

    Examples:
      "J309 735 Wn" → "0309735"   (J=0, spaces stripped, unit suffix dropped)
      "0305 735"    → "0305735"
    """
    text = text.translate(_SEG7_SUBS)
    # Remove spaces that EasyOCR inserts between consecutive digit characters
    # (7-segment display segments are often separated by a small gap)
    text = re.sub(r'(?<=\d) +(?=\d)', '', text)
    return text


def _extract_numeric(results: list, img_size: tuple = (0, 0)) -> str | None:
    """
    Pick the best meter-reading candidate from EasyOCR detail results.

    Each result is a (bbox, text, confidence) tuple.
    Scoring rules:
    - Base score  = OCR confidence (0..1)
    - +0.3 for 4-8 digit count      → typical meter display range
    - +0.2 for exactly one decimal   → rollover/drum display with decimal
    - -0.4 for >8 digits             → serial number territory
    - -0.5 for date-like d.d.dd      → e.g. installation date 25.7.18
    - +0.1/+0.4 bbox height bonus    → display digits are physically larger
                                       than label or serial-number text
    """

    img_h = img_size[1] if img_size else 0
    candidates = []

    for (bbox, text, conf) in results:
        # Keep only texts that are mostly numeric after stripping non-digit/. chars
        cleaned = re.sub(r"[^\d.,]", "", text)
        if not cleaned:
            continue
        # Require at least half the original text to be digits (avoids pure label debris)
        digits_in_orig = sum(c.isdigit() for c in text)
        if len(text) > 0 and digits_in_orig / len(text) < 0.4:
            continue
        cleaned = cleaned.replace(",", ".").strip(".")
        if not cleaned:
            continue

        dots = cleaned.count(".")
        if dots > 1:
            # Multiple dots → date-like pattern (e.g. 25.7.18) → discard
            continue

        digits = re.sub(r"\D", "", cleaned)
        if len(digits) < 3:
            continue

        score = conf

        if len(digits) == 3:
            score -= 0.2  # years (202x), 3-digit codes → unlikely meter reading
        elif 4 <= len(digits) <= 8:
            score += 0.3
        elif len(digits) > 8:
            score -= 0.4

        if dots == 1:
            score += 0.2

        # Bounding-box height bonus: display digits span a larger fraction of
        # the image height than handwritten labels or serial-number segments.
        # bbox is [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
        if img_h > 0 and bbox:
            try:
                ys = [p[1] for p in bbox]
                h_ratio = (max(ys) - min(ys)) / img_h
                if h_ratio > 0.06:    # very large text → almost certainly the display
                    score += 0.4
                elif h_ratio > 0.025: # medium text → possible display
                    score += 0.1
                # small text (labels, fine print) gets no bonus
            except (TypeError, IndexError):
                pass

        candidates.append((score, cleaned))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _extract_serial_sync(filepath: Path) -> str | None:
    """
    Run OCR on a user-cropped serial number area.

    No LCD-specific preprocessing — the user has already isolated the serial label.
    Pipeline: grayscale → CLAHE → upscale → sharpen.
    Returns the best alphanumeric candidate, or None.
    """
    import cv2
    import numpy as np
    from PIL import Image, ImageOps

    try:
        _pil = ImageOps.exif_transpose(Image.open(filepath)).convert("RGB")
        img = cv2.cvtColor(np.array(_pil), cv2.COLOR_RGB2BGR)
    except Exception:
        img = cv2.imread(str(filepath))
    if img is None:
        return None

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4))
    enhanced = clahe.apply(gray)

    h, w = enhanced.shape[:2]
    upscaled = cv2.resize(enhanced, (w * 3, h * 3), interpolation=cv2.INTER_CUBIC)
    k = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    sharpened = cv2.filter2D(upscaled, -1, k)

    proc_path = filepath.with_suffix(".serial_proc.jpg")
    Image.fromarray(sharpened).save(proc_path, quality=95)

    try:
        reader = _get_reader()
        results = reader.readtext(
            str(proc_path),
            detail=1,
            paragraph=False,
            low_text=0.3,
            text_threshold=0.4,
            width_ths=0.9,
        )
    except Exception:
        return None
    finally:
        proc_path.unlink(missing_ok=True)

    candidates = []
    for (_, text, conf) in results:
        cleaned = re.sub(r'\s+', '', text)
        alnum_ratio = sum(c.isalnum() for c in cleaned) / max(len(cleaned), 1)
        if len(cleaned) >= 4 and alnum_ratio >= 0.5:
            candidates.append((conf, cleaned))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _run_ocr_on_file_sync(filepath: Path, already_cropped: bool = False) -> dict:
    """Synchronous OCR (EasyOCR only) — called from asyncio.to_thread by app.main."""
    proc_path = None
    img_size = (0, 0)
    try:
        reader = _get_reader()
        proc_path, img_size = _preprocess_image(filepath, already_cropped=already_cropped)
        results = reader.readtext(
            str(proc_path),
            detail=1,
            paragraph=False,
            # Parameters tuned for 7-segment LCD meter displays:
            # low_text=0.3  → detect faint/partial segments
            # text_threshold=0.5 → reduce false positives without missing digits
            # link_threshold=0.2 → join adjacent digit segments into one text box
            # width_ths=0.9 → merge horizontally adjacent text boxes (full number)
            low_text=0.3,
            text_threshold=0.5,
            link_threshold=0.2,
            width_ths=0.9,
        )
    except Exception as exc:
        logger.error("OCR failed: %s", exc)
        raise RuntimeError(f"OCR processing failed: {exc}") from exc
    finally:
        if proc_path and proc_path.exists():
            proc_path.unlink(missing_ok=True)

    # Apply 7-segment substitutions before scoring; keep raw text in response
    corrected_results = [
        (bbox, _fix_seven_segment(text), conf) for (bbox, text, conf) in results
    ]
    raw_texts = [
        {"text": text, "conf": round(float(conf), 3)}
        for (_, text, conf) in corrected_results
    ]
    detected = _extract_numeric(corrected_results, img_size=img_size)
    return {"raw_texts": raw_texts, "detected_value": detected, "detected_serial": None, "detection_method": "ocr"}
