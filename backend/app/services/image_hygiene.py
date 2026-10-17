"""Decode check and metadata removal for stored meter photos (F-032, F-033).

``sanitize_image(data)`` is called on every image before it is written:

* the image is decoded completely (``PIL.Image.load``); truncated, zeroed or
  signature-only data raises :class:`ImageUndecodable` and nothing is stored;
* location and device metadata (GPS IFD, maker notes, XMP, IPTC, comments,
  text chunks) is removed.  Only an allow-list survives: orientation and the
  date tags the app reads (``DateTime``, ``DateTimeOriginal``,
  ``DateTimeDigitized``).  JPEG, PNG and WEBP are rewritten at segment / chunk
  level, so the pixel data is not re-encoded; TIFF is re-encoded losslessly;
  GIF and BMP carry no location data and are kept byte for byte.

The result is deterministic, so a re-uploaded photo hashes to the same value.
"""
import io
import logging
import struct
import zlib

from PIL import Image

logger = logging.getLogger(__name__)

_EXIF_IFD = 0x8769
_KEEP_ROOT_TAGS = (274, 306)          # Orientation, DateTime
_KEEP_EXIF_TAGS = (36867, 36868)      # DateTimeOriginal, DateTimeDigitized


class ImageUndecodable(ValueError):
    """The bytes are not a complete, decodable image."""


def _clean_exif(source: Image.Exif) -> bytes | None:
    """TIFF-structured EXIF holding only the allow-listed tags, or None if there are none."""
    clean = Image.Exif()
    found = False
    for tag in _KEEP_ROOT_TAGS:
        value = source.get(tag)
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            clean[tag] = value
            found = True
    try:
        sub = source.get_ifd(_EXIF_IFD)
    except Exception:  # nosec B110 - a damaged sub-IFD only costs the date
        sub = {}
    for tag in _KEEP_EXIF_TAGS:
        value = sub.get(tag)
        if isinstance(value, str):
            clean.get_ifd(_EXIF_IFD)[tag] = value
            found = True
    if not found:
        return None
    raw = clean.tobytes()
    return raw[6:] if raw.startswith(b"Exif\x00\x00") else raw


# ── JPEG ──────────────────────────────────────────────────────────────────────

_JPEG_STANDALONE = {0x01, *range(0xD0, 0xD8)}


def _strip_jpeg(data: bytes, exif: bytes | None) -> bytes:
    if data[:2] != b"\xff\xd8":
        raise ImageUndecodable("not a JPEG")
    out = bytearray(b"\xff\xd8")
    pending_exif = b"" if exif is None else (
        b"\xff\xe1" + struct.pack(">H", len(exif) + 8) + b"Exif\x00\x00" + exif
    )
    pos, n = 2, len(data)
    while pos < n:
        if data[pos] != 0xFF:
            raise ImageUndecodable("bad JPEG marker")
        while pos < n and data[pos] == 0xFF:  # fill bytes
            pos += 1
        marker = data[pos]
        pos += 1
        if marker == 0xD9:                    # EOI before SOS: nothing to decode
            raise ImageUndecodable("JPEG without image data")
        if marker in _JPEG_STANDALONE:
            out += bytes((0xFF, marker))
            continue
        (length,) = struct.unpack(">H", data[pos:pos + 2])
        payload = data[pos + 2:pos + length]
        segment_end = pos + length
        if marker == 0xDA:                    # start of scan: rest is entropy data up to EOI
            if pending_exif:
                out += pending_exif
                pending_exif = b""
            out += data[pos - 2:segment_end]
            eoi = data.find(b"\xff\xd9", segment_end)
            # Trailing data after the first EOI (MPF previews, appended blobs) is dropped.
            out += data[segment_end:eoi + 2] if eoi != -1 else data[segment_end:]
            return bytes(out)
        keep = True
        if 0xE0 <= marker <= 0xEF:
            if marker == 0xE0:
                keep = True
            elif marker == 0xE2:
                keep = payload.startswith(b"ICC_PROFILE\x00")
            elif marker == 0xEE:
                keep = payload.startswith(b"Adobe")
            else:
                keep = False
        elif marker == 0xFE:                  # comment
            keep = False
        if marker == 0xE0:
            out += data[pos - 2:segment_end]
            if pending_exif:
                out += pending_exif
                pending_exif = b""
        elif keep:
            out += data[pos - 2:segment_end]
        pos = segment_end
    raise ImageUndecodable("JPEG without image data")


# ── PNG ───────────────────────────────────────────────────────────────────────

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_PNG_DROP = {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"}


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def _strip_png(data: bytes, exif: bytes | None) -> bytes:
    if data[:8] != _PNG_SIG:
        raise ImageUndecodable("not a PNG")
    out = bytearray(_PNG_SIG)
    pos = 8
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        end = pos + 12 + length
        if end > len(data):
            raise ImageUndecodable("truncated PNG chunk")
        if kind not in _PNG_DROP:
            out += data[pos:end]
            if kind == b"IHDR" and exif is not None:
                out += _png_chunk(b"eXIf", exif)
        pos = end
        if kind == b"IEND":
            break
    return bytes(out)


# ── WEBP ──────────────────────────────────────────────────────────────────────

def _strip_webp(data: bytes, exif: bytes | None) -> bytes:
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ImageUndecodable("not a WEBP")
    chunks: list[tuple[bytes, bytes]] = []
    pos, limit = 12, min(len(data), 8 + struct.unpack("<I", data[4:8])[0])
    has_vp8x = False
    while pos + 8 <= limit:
        kind = data[pos:pos + 4]
        (size,) = struct.unpack("<I", data[pos + 4:pos + 8])
        payload = data[pos + 8:pos + 8 + size]
        if len(payload) != size:
            raise ImageUndecodable("truncated WEBP chunk")
        pos += 8 + size + (size & 1)
        if kind in (b"EXIF", b"XMP "):
            continue
        if kind == b"VP8X":
            has_vp8x = True
        chunks.append((kind, payload))
    if exif is not None and has_vp8x:
        # EXIF goes after the image data (spec order: ... ALPH/VP8/VP8L/ANMF, EXIF, XMP).
        chunks.append((b"EXIF", exif))
    else:
        exif = None
    body = bytearray(b"WEBP")
    for kind, payload in chunks:
        if kind == b"VP8X":
            flags = payload[0] & ~0x0C          # clear EXIF (0x08) and XMP (0x04)
            if exif is not None:
                flags |= 0x08
            payload = bytes((flags,)) + payload[1:]
        body += kind + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) & 1 else b"")
    return b"RIFF" + struct.pack("<I", len(body)) + bytes(body)


# ── TIFF ──────────────────────────────────────────────────────────────────────

def _reencode_tiff(img: Image.Image, source_exif: Image.Exif) -> bytes:
    # Pillow applies a TIFF orientation tag to the pixels while loading, so the
    # tag must not be written back (it would rotate twice); only the date stays.
    clean = Image.Exif()
    value = source_exif.get(306)
    if isinstance(value, str):
        clean[306] = value
    buf = io.BytesIO()
    # copy() drops the TIFF tag dictionary the plugin would otherwise write back.
    img.copy().save(buf, "TIFF", compression="tiff_adobe_deflate", exif=clean)
    return buf.getvalue()


def _reencode(data: bytes, fmt: str, exif: bytes | None) -> bytes:
    """Fallback for files whose segment structure we cannot rewrite: decode and save again."""
    with Image.open(io.BytesIO(data)) as img:
        img.load()
        buf = io.BytesIO()
        kw: dict = {}
        if exif is not None:
            kw["exif"] = b"Exif\x00\x00" + exif
        icc = img.info.get("icc_profile")
        if icc:
            kw["icc_profile"] = icc
        if fmt == "JPEG":
            img.save(buf, "JPEG", quality=95, **kw)
        elif fmt == "WEBP":
            img.save(buf, "WEBP", lossless=True, **kw)
        else:
            img.save(buf, fmt, **kw)
        return buf.getvalue()


# ── entry point ───────────────────────────────────────────────────────────────

def sanitize_image(data: bytes) -> bytes:
    """Return ``data`` without location/device metadata; raise if it does not decode."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format
            exif = img.getexif()
            img.load()                      # full decode: truncated / broken data raises here
            if fmt == "TIFF":
                return _reencode_tiff(img, exif)
            clean = _clean_exif(exif)
    except ImageUndecodable:
        raise
    except Exception as exc:                # PIL raises OSError, SyntaxError, ValueError, ...
        raise ImageUndecodable("image cannot be decoded") from exc

    try:
        if fmt == "JPEG":
            return _strip_jpeg(data, clean)
        if fmt == "PNG":
            return _strip_png(data, clean)
        if fmt == "WEBP":
            return _strip_webp(data, clean)
    except Exception:                       # non-conformant structure that PIL still decodes
        logger.info("image structure not rewritable in place, re-encoding")
        try:
            return _reencode(data, fmt, clean)
        except Exception as exc:
            raise ImageUndecodable("image structure is malformed") from exc
    return data
