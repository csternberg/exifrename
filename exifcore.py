"""
exifcore.py - EXIF date reading and validation engine for exifrename.

The validity rules are vendored from metadata.py 1.3.1 (2026-10-26)
(check_exif, parse_taken, mark_doubtful, the date-taken tag choice in
probe_image). Keep them in step with metadata.py: an image that metadata.py
reports as "valid" must be "valid" here too.

Pillow is imported lazily, only when an image is read.
"""

import os
import re
from datetime import datetime

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp"}

DATE_FORMATS = ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M", "%Y:%m:%d")
MIN_PLAUSIBLE_YEAR = 1990
CREATION_TOLERANCE = 24 * 3600  # EXIF has no time zone; allow a day of slack


def clean_text(value):
    """Strip control characters (e.g. NUL padding in EXIF) and squeeze whitespace."""
    if not isinstance(value, str):
        return value
    return " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", value).split())


def friendly_error(e):
    """Turn an exception into a short message a user can act on."""
    if isinstance(e, PermissionError):
        return "Access denied or file is locked by another program"
    if isinstance(e, FileNotFoundError):
        return "File not found (moved or deleted during the scan)"
    if type(e).__name__ == "UnidentifiedImageError":
        return "Not a recognized image (corrupt or unsupported)"
    if "truncated" in str(e).lower():
        return "File is truncated or damaged"
    return str(e).strip() or type(e).__name__


def sniff_is_image(head):
    """True if the first bytes of a file are the signature of a supported image."""
    return (head.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a",
                             b"II*\x00", b"MM\x00*"))
            or (head[:2] == b"BM" and head[6:10] == b"\0\0\0\0")
            or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"))


def parse_taken(taken):
    """EXIF date text -> datetime, or None if it cannot be understood."""
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(taken[:19] if "%H" in fmt else taken[:10], fmt)
        except ValueError:
            continue
    return None


def check_exif(taken, earliest_ts, has_exif_modified=False):
    """Return "valid" or the reason the EXIF date taken cannot be trusted.

    earliest_ts: the earlier of the file's creation and modification time (epoch seconds).
    """
    if not taken:
        return ("no date taken (only the EXIF save date is present)" if has_exif_modified
                else "no date taken")
    if not re.search(r"[1-9]", taken.split()[0] if taken.split() else ""):
        return "date taken is blank/zero"  # e.g. 0000:00:00 00:00:00
    when = parse_taken(taken)
    if when is None:
        return f"date taken not understood ({taken})"
    if when.year < MIN_PLAUSIBLE_YEAR:
        return f"date taken before {MIN_PLAUSIBLE_YEAR}"
    if when > datetime.now():
        return "date taken is in the future"
    if earliest_ts is not None and when.timestamp() > earliest_ts + CREATION_TOLERANCE:
        return "date taken is after the file creation/modification date"
    return "valid"


def mark_doubtful(records, min_count, max_seconds):
    """Flag "valid" dates as doubtful when a folder holds more than min_count images
    whose dates all fall within max_seconds of each other (typically a reset or
    constant camera clock, or dates stamped by a batch tool). Returns (images, folders).
    Records need "folder", "taken" and "exif_status"."""
    if min_count <= 0:
        return 0, 0
    by_folder = {}
    for r in records:
        if r.get("exif_status") == "valid":
            by_folder.setdefault(r["folder"], []).append((parse_taken(r["taken"]), r))
    images = folders = 0
    for group in by_folder.values():
        if len(group) <= min_count:
            continue
        times = [t for t, _ in group]
        spread = (max(times) - min(times)).total_seconds()
        if spread < max_seconds:
            folders += 1
            images += len(group)
            for _, r in group:
                r["exif_status"] = (f"doubtful: {len(group)} images in this folder "
                                    f"have dates within {spread:.0f} s of each other")
    return images, folders


def plausible(when):
    return when is not None and when.year >= MIN_PLAUSIBLE_YEAR and when <= datetime.now()


def gps_datetime(exif):
    """The GPS date/time stamp (UTC) as a datetime, or None."""
    try:
        gps = exif.get_ifd(0x8825)
        day = parse_taken(str(gps[29]))
        h, m, s = (int(float(x)) for x in gps[7])
        return day.replace(hour=h, minute=m, second=s)
    except Exception:
        return None


def analyze_image(path):
    """Read the dates of one image. Never raises: a fatal problem ends up in
    rec["error"]. Returns a dict with:
      path, folder, name, taken (EXIF text), exif_status, taken_dt (datetime if valid),
      alt_dates [(label, datetime)] - other EXIF dates, plausible only,
      file_dt - the earlier of file creation/modification time."""
    rec = {"path": path, "folder": str(path.parent), "name": path.name, "alt_dates": []}
    try:
        st = path.stat()
        created = getattr(st, "st_birthtime", None) or (st.st_ctime if os.name == "nt" else None)
        earliest_ts = min(t for t in (created, st.st_mtime) if t)
        rec["file_dt"] = datetime.fromtimestamp(earliest_ts)
        if st.st_size == 0:
            rec["error"] = "Empty file (0 bytes)"
            return rec
        with open(path, "rb") as f:  # also catches locked / unreadable files early
            head = f.read(32)
    except Exception as e:
        rec["error"] = friendly_error(e)
        return rec
    if not sniff_is_image(head):
        rec["error"] = "Not an image file"
        return rec

    try:
        from PIL import Image
    except ImportError:
        rec["error"] = "Pillow is not installed (pip install pillow)"
        return rec
    try:
        Image.MAX_IMAGE_PIXELS = None  # only the header is read
        with Image.open(path) as img:
            taken = modified = None
            exif = None
            try:
                exif = img.getexif()
                sub = exif.get_ifd(0x8769)
                # Capture date only: DateTimeOriginal, else DateTimeDigitized. The plain
                # DateTime tag (306) is when software last saved the file.
                taken = sub.get(36867) or exif.get(36867) or sub.get(36868)
                modified = exif.get(306)
            except Exception:
                pass  # missing or damaged EXIF is not an error
    except Exception as e:
        rec["error"] = friendly_error(e)
        return rec

    taken = clean_text(str(taken)) if taken else None
    modified = clean_text(str(modified)) if modified else None
    rec["taken"] = taken
    rec["exif_status"] = check_exif(taken, earliest_ts, bool(modified))
    if rec["exif_status"] == "valid":
        rec["taken_dt"] = parse_taken(taken)
    if modified and re.search(r"[1-9]", modified.split()[0] if modified.split() else ""):
        when = parse_taken(modified)
        if plausible(when):
            rec["alt_dates"].append(("EXIF save date", when))
    when = gps_datetime(exif) if exif is not None else None
    if plausible(when):
        rec["alt_dates"].append(("GPS date stamp", when))
    return rec
