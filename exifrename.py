#!/usr/bin/env python3
"""
exifrename.py - rename (or copy) image files after their EXIF date.

Run "exifrename.py --help" for usage, "exifrename.py --check" to verify prerequisites.
"""

import sys

if sys.version_info < (3, 8):
    sys.exit("exifrename.py needs Python 3.8 or newer (found %s). Download: https://www.python.org/downloads/"
             % sys.version.split()[0])

import argparse
import csv
import fnmatch
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

import exifcore

__version__ = "1.0.0"
__date__ = "2026-10-29"
PROG = "exifrename.py"

MAP_NAME = "exifrename.map"
LOG_NAME = "exifrename.log"
DEFAULT_MASK = "EXIF-%YYYY%-%MM%-%DD%_%HH%-%II%-%SS%"
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
TAGS = {"YYYY", "YY", "MMMMMM", "MMMM", "MM", "DD", "HH", "HHHH", "II", "SS", "FILE", "FOLDER"}
INVALID_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class Reporter:
    """Results go to stdout, messages to stderr; both go to the log when -L is given."""

    def __init__(self, log_path=None):
        self.log = None
        if log_path:
            try:
                self.log = open(log_path, "a", encoding="utf-8")
                self.log.write(f"\n=== {PROG} {__version__} {datetime.now():%Y-%m-%d %H:%M:%S} "
                               f"{' '.join(sys.argv[1:])}\n")
            except OSError as e:
                print(f"Cannot write log {log_path}: {exifcore.friendly_error(e)}", file=sys.stderr)

    def _log(self, msg):
        if self.log:
            self.log.write(msg + "\n")
            self.log.flush()

    def line(self, msg):
        self._log(msg)
        try:
            print(msg)
        except BrokenPipeError:
            pass

    def warn(self, msg):
        self._log(msg)
        print(msg, file=sys.stderr)


# --------------------------------------------------------------------------
# prerequisites
# --------------------------------------------------------------------------

def run_check():
    """--check: print the state of every prerequisite. Returns exit code."""
    print(f"{PROG} {__version__} ({__date__})")
    print(f"Python {sys.version.split()[0]} at {sys.executable}\n")
    try:
        import PIL
        print(f"  [ OK ]  Pillow: {getattr(PIL, '__version__', 'unknown')}")
        print("\nAll prerequisites are installed.")
        return 0
    except ImportError:
        print("  [MISSING]  Pillow - needed to read EXIF dates")
        print(f'      Install: "{sys.executable}" -m pip install pillow')
        return 1


# --------------------------------------------------------------------------
# mask
# --------------------------------------------------------------------------

def parse_mask(mask):
    """Mask text -> list of ("lit", text) / ("tag", NAME). Raises ValueError."""
    tokens = []
    pos = 0
    lit = []
    pattern = re.compile(r"%[A-Za-z]+%|%%")
    while pos < len(mask):
        m = pattern.match(mask, pos)
        if m:
            text = m.group()
            if text == "%%":
                lit.append("%")
            else:
                name = text[1:-1].upper()
                if name not in TAGS:
                    raise ValueError(f"unknown tag {text} in mask (known: "
                                     + ", ".join("%" + t + "%" for t in sorted(TAGS)) + ")")
                if lit:
                    tokens.append(("lit", "".join(lit)))
                    lit = []
                tokens.append(("tag", name))
            pos = m.end()
        elif mask[pos] == "%":
            raise ValueError(f"stray % at position {pos + 1} in mask (write %% for a literal %)")
        else:
            lit.append(mask[pos])
            pos += 1
    if lit:
        tokens.append(("lit", "".join(lit)))
    bad = [t for k, t in tokens if k == "lit" and INVALID_NAME_CHARS.search(t)]
    if bad:
        raise ValueError('mask text may not contain any of  < > : " / \\ | ? *')
    if not any(k == "tag" for k, _ in tokens) and not tokens:
        raise ValueError("mask is empty")
    return tokens


def render_mask(tokens, when, stem, folder):
    """Expand a parsed mask for one file. AM/PM follows the 12-hour hour, and any
    minute/second tags directly after it."""
    values = {
        "YYYY": f"{when.year:04d}", "YY": f"{when.year % 100:02d}",
        "MMMMMM": MONTHS[when.month - 1], "MMMM": MONTHS[when.month - 1][:3],
        "MM": f"{when.month:02d}", "DD": f"{when.day:02d}",
        "HH": f"{when.hour:02d}", "HHHH": f"{when.hour % 12 or 12:02d}",
        "II": f"{when.minute:02d}", "SS": f"{when.second:02d}",
        "FILE": stem, "FOLDER": folder,
    }
    ampm = "am" if when.hour < 12 else "pm"
    out = []
    i = 0
    while i < len(tokens):
        kind, val = tokens[i]
        out.append(val if kind == "lit" else values[val])
        if kind == "tag" and val == "HHHH":
            while i + 1 < len(tokens) and tokens[i + 1] in (("tag", "II"), ("tag", "SS")):
                i += 1
                out.append(values[tokens[i][1]])
            out.append(ampm)
        i += 1
    return "".join(out).strip().rstrip(".")


# --------------------------------------------------------------------------
# file discovery
# --------------------------------------------------------------------------

def has_wildcard(text):
    return any(c in text for c in "*?[")


def collect_files(patterns, current, recursive, cwd, rep):
    """Return the sorted list of files to process."""
    found = {}

    def add(p):
        if p.name.lower() in (MAP_NAME, LOG_NAME):
            return
        found.setdefault(os.path.normcase(str(p.resolve())), p)

    def scan(base, test):
        walker = os.walk(base, onerror=lambda e: rep.warn(
            f"Warning: cannot read folder {e.filename}: {exifcore.friendly_error(e)}")) \
            if recursive else [(str(base), [], _listfiles(base, rep))]
        for d, _, files in walker:
            for f in files:
                if test(f):
                    p = Path(d) / f
                    if p.is_file():
                        add(p)

    if patterns:
        for pat in patterns:
            p = Path(pat)
            if p.is_file() and not recursive:
                add(p)
                continue
            base = p.parent if str(p.parent) != "." else cwd
            if not base.is_dir():
                rep.warn(f"Folder not found: {base}")
                continue
            low = p.name.lower()
            before = len(found)
            scan(base, lambda n, low=low: fnmatch.fnmatchcase(n.lower(), low))
            if len(found) == before:
                rep.warn(f"No files match {pat}")
    else:
        scan(cwd, lambda n: Path(n).suffix.lower() in exifcore.IMAGE_EXTENSIONS)
    return sorted(found.values(), key=lambda p: (str(p.parent).lower(), p.name.lower()))


def _listfiles(base, rep):
    try:
        return [e for e in os.listdir(base)]
    except OSError as e:
        rep.warn(f"Warning: cannot read folder {base}: {exifcore.friendly_error(e)}")
        return []


# --------------------------------------------------------------------------
# renaming / copying
# --------------------------------------------------------------------------

def key_of(path):
    return os.path.normcase(str(path))


def pick_target(folder, stem, ext, claimed, own):
    """First free name stem+ext, stem_001+ext, ... in folder. A name is free if no
    file of this run claimed it and it is not on disk (the file's own name is fine)."""
    n = 0
    while True:
        name = stem + ext if n == 0 else f"{stem}_{n:03d}{ext}"
        cand = folder / name
        k = key_of(cand)
        if k not in claimed and (not cand.exists() or (own is not None and k == key_of(own))):
            claimed.add(k)
            return cand
        n += 1


def safe_rename(src, dst):
    if dst.exists() and key_of(dst) != key_of(src):
        raise FileExistsError(f"{dst.name} already exists")
    os.rename(src, dst)


def append_map(folder, new_name, old_name):
    mp = folder / MAP_NAME
    new_file = not mp.exists()
    with open(mp, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["new_name", "old_name"])
        w.writerow([new_name, old_name])


def choose_date(rec, force):
    """-> (datetime, source text) or (None, reason)."""
    if rec["exif_status"] == "valid":
        return rec["taken_dt"], "EXIF date taken"
    if not force:
        return None, rec["exif_status"]
    if rec["alt_dates"]:
        label, when = rec["alt_dates"][0]
        return when, f"{label} (EXIF date taken: {rec['exif_status']})"
    return rec["file_dt"], f"file date (EXIF date taken: {rec['exif_status']})"


def display(path, cwd):
    try:
        return str(path.relative_to(cwd))
    except ValueError:
        return str(path)


def run_rename(args, tokens, cwd, rep):
    files = collect_files(args.patterns, args.current, args.recursive, cwd, rep)
    if not files:
        rep.warn("No files found" + ("" if args.recursive or args.patterns else
                 " (add -R to include subfolders)") + ".")
        return 1

    records = [exifcore.analyze_image(p) for p in files]
    ok = [r for r in records if "error" not in r]
    if args.doubtful_count:
        images, folders = exifcore.mark_doubtful(ok, args.doubtful_count, args.doubtful_seconds)
        if images:
            rep.warn(f"{images} images in {folders} folder{'s' * (folders != 1)} "
                     f"have doubtful EXIF dates.")

    copy_mode = args.copy is not None or args.copytree is not None
    verb_done, verb_dry = ("copied", "would copy") if copy_mode else ("renamed", "would rename")
    claimed = set()
    counts = {"done": 0, "skipped": 0, "failed": 0, "unreadable": 0}
    status = 0

    for rec in records:
        path = rec["path"]
        shown = display(path, cwd)
        if "error" in rec:
            counts["unreadable"] += 1
            rep.line(f"skipped: {shown}: {rec['error']}")
            continue
        when, source = choose_date(rec, args.force)
        if when is None:
            counts["skipped"] += 1
            rep.line(f"skipped: {shown}: {source}")
            continue

        stem = render_mask(tokens, when, path.stem, path.parent.name)
        if not stem or INVALID_NAME_CHARS.search(stem):
            counts["failed"] += 1
            status = 1
            rep.line(f"error: {shown}: mask gives an unusable file name ({stem!r})")
            continue

        if args.copytree is not None:
            try:
                rel = path.parent.relative_to(cwd)
            except ValueError:
                rel = Path(path.parent.name)
            dest_dir = Path(args.copytree) / rel
        elif args.copy:
            dest_dir = Path(args.copy)
        else:
            dest_dir = path.parent
        dest_dir = dest_dir if dest_dir.is_absolute() else cwd / dest_dir

        target = pick_target(dest_dir, stem, path.suffix, claimed, None if copy_mode else path)
        if not copy_mode and key_of(target) == key_of(path) and target.name == path.name:
            counts["skipped"] += 1
            rep.line(f"skipped: {shown}: already has the right name")
            continue

        label = f"{shown} -> {display(target, cwd)}  [{source}]"
        if args.dry_run:
            counts["done"] += 1
            rep.line(f"{verb_dry}: {label}")
            continue
        try:
            if copy_mode:
                dest_dir.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    raise FileExistsError(f"{target.name} already exists")
                shutil.copy2(path, target)
            else:
                safe_rename(path, target)
                try:
                    append_map(path.parent, target.name, path.name)
                except OSError as e:
                    rep.warn(f"Warning: cannot update {MAP_NAME} in {path.parent}: "
                             f"{exifcore.friendly_error(e)}")
        except Exception as e:
            counts["failed"] += 1
            status = 1
            rep.line(f"error: {shown}: {exifcore.friendly_error(e)}")
            continue
        counts["done"] += 1
        rep.line(f"{verb_done}: {label}")

    rep.warn(f"\n{counts['done']} {verb_dry if args.dry_run else verb_done}, "
             f"{counts['skipped']} skipped, {counts['unreadable']} unreadable, "
             f"{counts['failed']} failed.")
    return status


# --------------------------------------------------------------------------
# undo
# --------------------------------------------------------------------------

def read_map(mp):
    with open(mp, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.reader(f) if len(r) >= 2]
    return rows[1:] if rows and rows[0][:2] == ["new_name", "old_name"] else rows


def write_map(mp, rows):
    if not rows:
        mp.unlink()
        return
    with open(mp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["new_name", "old_name"])
        w.writerows(rows)


def run_undo(args, cwd, rep):
    if args.recursive:
        folders = []
        for d, _, files in os.walk(cwd):
            if MAP_NAME in files:
                folders.append(Path(d))
    else:
        folders = [cwd] if (cwd / MAP_NAME).exists() else []
    if not folders:
        rep.warn(f"No {MAP_NAME} found" + ("" if args.recursive else " (add -R to look in subfolders)") + ".")
        return 1

    pats = [Path(p).name.lower() for p in args.patterns]
    undone = failed = 0
    status = 0
    for folder in sorted(folders):
        mp = folder / MAP_NAME
        try:
            rows = read_map(mp)
        except Exception as e:
            rep.warn(f"Cannot read {mp}: {exifcore.friendly_error(e)}")
            status = 1
            continue
        keep = []
        for row in reversed(rows):
            new, old = row[0], row[1]
            if pats and not any(fnmatch.fnmatchcase(new.lower(), p) for p in pats):
                keep.append(row)
                continue
            src, dst = folder / new, folder / old
            label = f"{display(src, cwd)} -> {display(dst, cwd)}"
            if not src.exists():
                rep.line(f"skipped: {display(src, cwd)}: no longer exists")
                keep.append(row)
                continue
            if args.dry_run:
                rep.line(f"would restore: {label}")
                keep.append(row)
                undone += 1
                continue
            try:
                safe_rename(src, dst)
            except Exception as e:
                failed += 1
                status = 1
                rep.line(f"error: {label}: {exifcore.friendly_error(e)}")
                keep.append(row)
                continue
            undone += 1
            rep.line(f"restored: {label}")
        if not args.dry_run:
            try:
                write_map(mp, list(reversed(keep)))
            except OSError as e:
                rep.warn(f"Warning: cannot update {mp}: {exifcore.friendly_error(e)}")
    rep.warn(f"\n{undone} {'would be restored' if args.dry_run else 'restored'}, {failed} failed.")
    return status


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------

HELP_EPILOG = r"""
targets (one of these is required):
  PATTERN ...     file names or wildcards, case-insensitive (photo*.jpg, *.JPG, dir\*.png)
  -C              all image files in the current folder
  -R              recurse into subfolders; with PATTERN, matches PATTERN in every subfolder,
                  without it, all image files (-C is implied and ignored when PATTERN is given)

mask (-M), default  EXIF-%YYYY%-%MM%-%DD%_%HH%-%II%-%SS%
  %YYYY% year 2026   %YY% year 26   %MMMMMM% month November   %MMMM% month Nov
  %MM% month 11      %DD% day 29    %HH% hour 00-23   %HHHH% hour 01-12 (am/pm added)
  %II% minute 34     %SS% second 59
  %file% original name without extension   %folder% name of the folder holding the file
  %%  a literal %.  Any other text is used as is. The extension is kept.
  %HHHH% adds am/pm after itself and any %II%/%SS% directly following it: 043459pm.

EXIF dates:
  A date is used when valid: DateTimeOriginal/Digitized present, not blank, not before
  1990, not in the future and not after the file's creation/modification date. Dates
  of a folder with a burst of near-identical dates are "doubtful" and treated as invalid.
  Same rules as metadata.py. Invalid ones are skipped, unless -F is given: then the EXIF
  save date, the GPS date stamp, and finally the file's creation/modification date is used.

names and undo:
  Equal names get _001, _002, ... Existing files are never overwritten.
  Renames are recorded in exifrename.map in each folder; -U restores the old names.
  -L and --copy take an optional value: put them last, or write -L=file / --copy=folder.

exit codes: 0 ok, 1 error or nothing found, 2 usage, 130 interrupted.
"""


def normalize_argv(argv):
    """Make switches case-insensitive: -R -> -r, --Dry-Run -> --dry-run."""
    out = []
    for i, a in enumerate(argv):
        if a == "--":
            out.extend(argv[i:])
            break
        if a.startswith("--"):
            name, eq, val = a.partition("=")
            out.append(name.lower() + eq + val)
        elif re.fullmatch(r"-[A-Za-z?]+", a):
            out.append(a.lower())
        else:
            out.append(a)
    return out


def build_parser():
    ap = argparse.ArgumentParser(
        prog=PROG, add_help=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Rename (or copy) image files after their EXIF date.",
        epilog=HELP_EPILOG)
    ap.add_argument("patterns", nargs="*", metavar="PATTERN",
                    help="files to process (wildcards allowed, case-insensitive)")
    ap.add_argument("-c", "--current", action="store_true",
                    help="process all image files in the current folder")
    ap.add_argument("-r", "--recursive", action="store_true",
                    help="include subfolders")
    ap.add_argument("-m", "--mask", default=DEFAULT_MASK, metavar="MASK",
                    help="new name pattern (default: %(default)s)")
    ap.add_argument("-f", "--force", action="store_true",
                    help="rename even when the EXIF date is invalid (falls back to other "
                         "EXIF dates, then the file date)")
    ap.add_argument("--copy", nargs="?", const="", metavar="FOLDER",
                    help="copy instead of rename; copies go to FOLDER (default: beside the original)")
    ap.add_argument("--copytree", metavar="FOLDER",
                    help="like --copy FOLDER, but recreate the folder tree relative to the current folder")
    ap.add_argument("-u", "--undo", action="store_true",
                    help="restore the old names from exifrename.map (current folder, or all with -R)")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would happen; change nothing")
    ap.add_argument("-l", "--log", nargs="?", const=LOG_NAME, metavar="FILE",
                    help=f"also write the output to a log file (default: {LOG_NAME})")
    ap.add_argument("--doubtful-count", type=int, default=20, metavar="N",
                    help="mark EXIF dates doubtful when a folder has more than N images "
                         "taken within --doubtful-seconds of each other (default 20; 0 = off)")
    ap.add_argument("--doubtful-seconds", type=int, default=60, metavar="S",
                    help="time span for the doubtful check (default 60)")
    ap.add_argument("--check", action="store_true",
                    help="check prerequisites, show how to install missing ones, and exit")
    ap.add_argument("-v", "--version", action="version",
                    version=f"{PROG} {__version__} ({__date__})")
    ap.add_argument("-h", "-?", "--help", action="help", help="show this help and exit")
    return ap


def fix_optional_values(args):
    """-L and --copy take an optional value that can swallow a file pattern
    (-L *.jpg). A value with a wildcard, or naming an image file, is a pattern."""
    for name, const in (("log", LOG_NAME), ("copy", "")):
        val = getattr(args, name)
        if val and (has_wildcard(val) or (Path(val).is_file()
                                          and Path(val).suffix.lower() in exifcore.IMAGE_EXTENSIONS)):
            args.patterns.insert(0, val)
            setattr(args, name, const)


def main():
    for stream in (sys.stdout, sys.stderr):  # odd file names must not crash printing
        try:
            stream.reconfigure(errors="replace")
        except AttributeError:
            pass
    ap = build_parser()
    args = ap.parse_intermixed_args(normalize_argv(sys.argv[1:]))
    if args.check:
        return run_check()
    fix_optional_values(args)

    if args.copy is not None and args.copytree is not None:
        ap.error("use either --copy or --copytree, not both")
    if args.undo and (args.copy is not None or args.copytree is not None or args.force):
        ap.error("-U cannot be combined with --copy, --copytree or -F")
    if not (args.patterns or args.current or args.recursive or args.undo):
        ap.print_help(sys.stderr)
        print(f"\n{PROG}: give a PATTERN, -C or -R.", file=sys.stderr)
        return 2
    try:
        tokens = parse_mask(args.mask)
    except ValueError as e:
        print(f"{PROG}: bad mask: {e}", file=sys.stderr)
        return 2

    rep = Reporter(args.log)
    cwd = Path.cwd()
    try:
        if args.undo:
            return run_undo(args, cwd, rep)
        return run_rename(args, tokens, cwd, rep)
    except KeyboardInterrupt:
        rep.warn("\nInterrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
