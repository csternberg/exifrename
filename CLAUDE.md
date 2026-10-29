# CLAUDE.md

## Project

`exifrename.py` renames/copies images after their EXIF date using a `%tag%` mask. User docs: `README.md`.
Sibling project of `metadata.py` (`../metadata-main`), whose EXIF validity rules it shares.

## Environment

- Windows is the primary platform; keep macOS/Linux working. Python 3.8+: no `match`, no `X | Y` unions.
- Pillow is the only dependency, imported lazily (in `exifcore.analyze_image`), never at top level.

## Commands

```
python exifrename.py --check
python exifrename.py -C --dry-run
python -m unittest discover tests
```

## Architecture

- `exifcore.py`: vendored from metadata.py 1.3.1 (`check_exif`, `parse_taken`, `mark_doubtful`, tag choice
  in `probe_image`). When metadata.py changes its validity rules, port them here and update the README note.
  `analyze_image` never raises: problems go in `rec["error"]`.
- `exifrename.py`: `parse_mask`/`render_mask` (mask), `collect_files`, `choose_date` (valid date, or -F fallback),
  `pick_target` (collision `_001` logic), `run_rename`, `run_undo`, `build_parser`.
- `normalize_argv` lower-cases switches, so **no two options may differ only by case**.
- `-L` / `--copy` have optional values; `fix_optional_values` rescues a swallowed file pattern.

## Conventions

- Bump `__version__`/`__date__`, add a README changelog entry, and update the man page `.TH` line for
  every user-visible change.
- Option changes: update `build_parser`, `HELP_EPILOG`, the README table and `man/exifrename.py.1` together.
- User messages go to stderr; per-file results to stdout (and the log). Never overwrite files; undo must
  stay possible (`exifrename.map`).
- Exit codes: 0 ok, 1 error, 2 usage, 130 interrupted.
