# exifrename

`exifrename.py` renames (or copies) image files after the date they were taken, read
from their EXIF data, using a name mask you control. Files with an untrustworthy EXIF
date are left alone unless you force them. Every rename is recorded so it can be undone.

The EXIF validity rules are the same as in the companion tool `metadata.py`
(vendored in `exifcore.py`, from metadata.py 1.3.1).

## Requirements

- Python 3.8 or newer
- [Pillow](https://python-pillow.org/) (`python -m pip install pillow`)

Check with `python exifrename.py --check`.

## Usage

```
python exifrename.py [options] PATTERN ...
python exifrename.py [options] -C
python exifrename.py [options] -R [PATTERN ...]
python exifrename.py -U [-R] [PATTERN ...]
```

One of `PATTERN`, `-C` or `-R` is required (otherwise the help is shown and the exit code is 2).
Switches are case-insensitive (`-R` = `-r`), and every one has a short and a long (unix style) name.

| Option | Meaning |
|---|---|
| `PATTERN ...` | Files to process. Wildcards and case-insensitive (`*.jpg` also finds `A.JPG`). Shell-expanded names work too. |
| `-C`, `--current` | All image files in the current folder. |
| `-R`, `--recursive` | Include subfolders. With `PATTERN` it selects matching files in every subfolder; without it, all images. With a `PATTERN`, `-C` is ignored. |
| `-M MASK`, `--mask` | New name, see below. Default `EXIF-%YYYY%-%MM%-%DD%_%HH%-%II%-%SS%`. |
| `-F`, `--force` | Rename even if the EXIF date is invalid (see below). |
| `-D [FOLDER]`, `--copy` | Copy instead of rename. Copies go to `FOLDER`, or beside the originals if no folder is given. Files that are not renamed (invalid EXIF without `-F`, unreadable) are copied too, with their original name. |
| `-T FOLDER`, `--copytree` | Like `--copy FOLDER`, but recreates the sub-folder tree relative to the current folder: `./folder1/folder2/x.jpg` -> `FOLDER/folder1/folder2/`. Files in the current folder go directly in `FOLDER`. |
| `-O`, `--onlyvalid` | With `--copy`/`--copytree`: copy only files with a valid EXIF date; the others are skipped. Ignored with `-F`. A usage error without `--copy`/`--copytree`. |
| `-U`, `--undo` | Restore old names from `exifrename.map` (current folder, or all subfolders with `-R`). `PATTERN` limits it to matching new names. |
| `-N`, `--dry-run` | Show what would happen; change nothing (works with rename, copy and undo). |
| `-L [FILE]`, `--log` | Also write the output to a log (default `exifrename.log`, appended). |
| `-X N`, `--doubtful-count`; `-S S`, `--doubtful-seconds` | Doubtful-date check, as in metadata.py (default 20 images within 60 s; `0` turns it off). |
| `-K`, `--check` | Verify prerequisites. |
| `-V`, `--version` | Show the version. |
| `-H`, `-?`, `--help` | Show help. |

`-L` and `--copy` take an optional value, so put them last on the command line (or write `-L=file`,
`--copy=folder`). A value with a wildcard, or naming an existing image, is treated as a `PATTERN`.

### Mask

Text with `%tags%` (case-insensitive). The file extension is always kept.

| Tag | Meaning | Example |
|---|---|---|
| `%YYYY%` / `%YY%` | year, 4 / 2 digits | 2026 / 26 |
| `%MMMMMM%` / `%MMMM%` | month name, full / short (always English) | November / Nov |
| `%MM%` | month, 2 digits | 11 |
| `%DD%` | day | 29 |
| `%HH%` | hour, 24-hour | 16 |
| `%HHHH%` | hour, 12-hour, 2 digits; `am`/`pm` is added | 04 ... pm |
| `%II%` | minutes | 34 |
| `%SS%` | seconds | 59 |
| `%file%` | original name without extension | file1 |
| `%folder%` | name of the folder the file is in | holiday |
| `%%` | a literal `%` | % |

Any other text is used as it is, except that `< > : " / \ | ? *` are not allowed. An unknown tag
or a stray `%` is a usage error (exit 2).
`am`/`pm` goes after `%HHHH%` and any `%II%`/`%SS%` directly after it:

```
-M %file%-%DD%d%MM%m%YYYY%y-%HH%h_%II%m_%SS%s   file1.jpg -> file1-29d11m2026y-16h_34m_59s.jpg
-M Newfiles_%YY%%MMMM%%DD%_%HH%%II%%SS%-EXIF    file1.jpg -> Newfiles_26Nov29_163459-EXIF.jpg
-M Newfiles_%YY%%MMMM%%DD%_%HHHH%%II%%SS%-EXIF  file1.jpg -> Newfiles_26Nov29_043459pm-EXIF.jpg
```

### When is the EXIF date valid?

Same rules as `metadata.py`: the date taken (DateTimeOriginal, else DateTimeDigitized; the plain
DateTime save date is ignored) must exist, not be blank/zero, be understood, not be before 1990, not be
in the future, and not be later than the file's creation/modification date (one day of slack).
Also, if a folder has more than 20 images whose dates fall within 60 s of each other, they are
"doubtful" (a stuck or reset camera clock) and count as invalid. Invalid files are reported as
`skipped` with the reason.

With `-F` an invalid file gets its date from the first of: the EXIF save date, the GPS date stamp (UTC),
the earlier of the file's creation and modification time. The log line says which was used.
Files that are not readable images are never renamed, even with `-F`.

### Names, collisions, undo

- Two files that would get the same name: the second becomes `name_001.ext`, then `_002` ... Existing
  files are never overwritten. Running again is safe: a file that already has its right name is left alone.
- Each rename is appended to `exifrename.map` (CSV, `new_name,old_name`) in the folder of the file.
  `--copy`/`--copytree` write no map, since the originals are untouched.
- `-U` renames back, last rename first, and removes the restored lines from the map (the map file is
  deleted when empty). It will not overwrite an existing file.

### Exit codes

0 ok, 1 error (or nothing found), 2 usage, 130 interrupted. Skipped files are not errors.
Per-file results are printed on stdout; summary and warnings on stderr.

## Tests

```
python -m unittest discover tests
```

## Changelog

### 1.2.0 (2026-10-30)
- New `-O`/`--onlyvalid` with `--copy`/`--copytree`: only files with a valid EXIF date are copied.

### 1.1.0 (2026-10-30)
- `--copy`/`--copytree` also copy files that are not renamed, under their original name.
- Every option now has a short and a long name: `-D/--copy`, `-T/--copytree`, `-N/--dry-run`, `-K/--check`, `-X/--doubtful-count`, `-S/--doubtful-seconds`.

### 1.0.0 (2026-10-29)
- First release.
