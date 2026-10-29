"""Run with:  python -m unittest discover tests   (needs Pillow)"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import exifrename  # noqa: E402


def make_jpg(path, taken=None, size=(8, 8)):
    img = Image.new("RGB", size, "red")
    exif = Image.Exif()
    if taken:
        exif.get_ifd(0x8769)[36867] = taken
        exif[306] = "2020:01:01 00:00:00"
    img.save(path, "JPEG", exif=exif)


def run(cwd, *args):
    p = subprocess.run([sys.executable, str(ROOT / "exifrename.py"), *args], cwd=cwd,
                       capture_output=True, text=True, encoding="utf-8")
    return p.returncode, p.stdout, p.stderr


class MaskTests(unittest.TestCase):
    def render(self, mask, when_text="2026:11:29 16:34:59", stem="file1", folder="pics"):
        from exifcore import parse_taken
        return exifrename.render_mask(exifrename.parse_mask(mask), parse_taken(when_text), stem, folder)

    def test_default(self):
        self.assertEqual(self.render(exifrename.DEFAULT_MASK), "EXIF-2026-11-29_16-34-59")

    def test_examples(self):
        self.assertEqual(self.render("%file%-%DD%d%MM%m%YYYY%y-%HH%h_%II%m_%SS%s"),
                         "file1-29d11m2026y-16h_34m_59s")
        self.assertEqual(self.render("Newfiles_%YY%%MMMM%%DD%_%HH%%II%%SS%-EXIF"),
                         "Newfiles_26Nov29_163459-EXIF")
        self.assertEqual(self.render("Newfiles_%YY%%MMMM%%DD%_%HHHH%%II%%SS%-EXIF"),
                         "Newfiles_26Nov29_043459pm-EXIF")

    def test_misc(self):
        self.assertEqual(self.render("%folder%_%MMMMMM%_100%%"), "pics_November_100%")
        self.assertEqual(self.render("%HHHH%", "2026:11:29 00:05:00"), "12am")

    def test_errors(self):
        for bad in ("%nope%", "50%", "a/b", "a:%DD%"):
            with self.assertRaises(ValueError):
                exifrename.parse_mask(bad)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def names(self, sub="."):
        return sorted(p.name for p in (self.d / sub).iterdir() if p.is_file())

    def test_rename_collision_undo(self):
        make_jpg(self.d / "a.jpg", "2020:05:06 07:08:09")
        make_jpg(self.d / "b.JPG", "2020:05:06 07:08:09")
        make_jpg(self.d / "c.jpg")  # no EXIF
        (self.d / "junk.png").write_bytes(b"not an image")
        code, out, err = run(self.d, "-C")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.names(), ["EXIF-2020-05-06_07-08-09.jpg",
                                        "EXIF-2020-05-06_07-08-09_001.JPG",
                                        "c.jpg", "exifrename.map", "junk.png"])
        code, out, err = run(self.d, "-C")  # idempotent
        self.assertEqual(self.names().count("c.jpg"), 1)
        self.assertIn("already has the right name", out)
        code, out, err = run(self.d, "-U")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.names(), ["a.jpg", "b.JPG", "c.jpg", "junk.png"])

    def test_dry_run_changes_nothing(self):
        make_jpg(self.d / "a.jpg", "2020:05:06 07:08:09")
        code, out, _ = run(self.d, "*.JPG", "--dry-run")
        self.assertIn("would rename", out)
        self.assertEqual(self.names(), ["a.jpg"])

    def test_force_uses_file_date(self):
        make_jpg(self.d / "c.jpg")
        run(self.d, "-C")
        self.assertEqual(self.names(), ["c.jpg"])
        run(self.d, "-C", "-F", "-M", "X_%YYYY%")
        self.assertTrue(self.names()[0].startswith("X_20"))

    def test_recursive_and_copytree(self):
        (self.d / "s1" / "s2").mkdir(parents=True)
        make_jpg(self.d / "a.jpg", "2020:05:06 07:08:09")
        make_jpg(self.d / "s1" / "s2" / "b.jpg", "2021:05:06 07:08:09")
        code, out, err = run(self.d, "-R", "--copytree", "out/x", "-M", "%folder%-%YYYY%")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.names(), ["a.jpg"])
        self.assertEqual(self.names("out/x"), [self.d.name + "-2020.jpg"])
        self.assertTrue((self.d / "out" / "x" / "s1" / "s2").is_dir())
        self.assertEqual(self.names("out/x/s1/s2"), ["s2-2021.jpg"])

    def test_copy_keeps_unrenamed(self):
        make_jpg(self.d / "a.jpg", "2020:05:06 07:08:09")
        make_jpg(self.d / "c.jpg")  # no EXIF
        code, out, err = run(self.d, "-C", "-D", "out")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.names("out"), ["EXIF-2020-05-06_07-08-09.jpg", "c.jpg"])
        self.assertEqual(self.names(), ["a.jpg", "c.jpg"])

    def test_onlyvalid(self):
        make_jpg(self.d / "a.jpg", "2020:05:06 07:08:09")
        make_jpg(self.d / "c.jpg")  # no EXIF
        code, out, err = run(self.d, "-C", "-D", "out", "-O")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.names("out"), ["EXIF-2020-05-06_07-08-09.jpg"])
        code, out, err = run(self.d, "-C", "-O", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("ignored", err)
        self.assertIn("would rename", out)

    def test_usage_errors(self):
        self.assertEqual(run(self.d)[0], 2)
        self.assertEqual(run(self.d, "-C", "-M", "%bad%")[0], 2)


if __name__ == "__main__":
    unittest.main()
