"""check_filename_safety - guards the filename-safety class.

Section titles are untrusted text that become file names. The properties that
matter are computable, so they are asserted over a hostile corpus instead of
being re-read in review: a name never leaves the output directory, never
collides case-insensitively, never hits a Windows reserved name, and never
exceeds the byte cap.
"""
import re
import tempfile
import unicodedata
import unittest
from pathlib import Path

from pdf_toc_splitter.core import Entry, plan, safe_name

RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
HOSTILE = [
    "../../etc/passwd", "..\\..\\windows\\system32", "C:\\temp\\x", "/absolute/path", "a/b", "a\\b",
    "CON", "con", "con.txt", "NUL", "aux.tar.gz", "COM1", "lpt9", "Com5.log",
    "title.", "title ", "  spaced  ", "...", "..", ".", "", "   ",
    "a\x00b", "a\x1fb", "tab\there", "line\nbreak", "del\x7f",
    "a:b", "a?b*c", 'say "hi"', "<tag>|pipe",
    "a" * 300, "ắ" * 300, "Chương 1 — Giới thiệu " * 20, "✍️" * 100,
    "Cafe\u0301", "Caf\u00e9",           # NFD and NFC spellings of one name
    "Intro", "INTRO", "intro",           # case-insensitive duplicates
    "A", "A", "A (2)",                   # duplicates whose suffix collides with a real title
    "Untitled", "Tiêu đề bình thường",
]
MAX_STEM_BYTES = 180


class SafeName(unittest.TestCase):
    def test_every_property_over_the_hostile_corpus(self):
        for title in HOSTILE:
            name = safe_name(title)
            with self.subTest(title=title, name=name):
                self.assertTrue(name)
                self.assertNotIn(name, {".", ".."})
                self.assertIsNone(re.search(r'[<>:"/\\|?*\x00-\x1f\x7f]', name))
                self.assertEqual(name, name.strip(" ."))
                self.assertNotIn(name.split(".")[0].upper(), RESERVED)
                self.assertLessEqual(len(name.encode("utf-8")), MAX_STEM_BYTES)
                self.assertEqual(name, unicodedata.normalize("NFC", name))

    def test_vietnamese_text_survives(self):
        self.assertEqual(safe_name("Tiêu đề bình thường"), "Tiêu đề bình thường")
        self.assertEqual(safe_name("Cafe\u0301"), "Caf\u00e9")

    def test_the_cut_never_splits_a_character(self):
        name = safe_name("ắ" * 300)
        name.encode("utf-8")  # raises if a surrogate or half character slipped through
        self.assertEqual(set(name), {"ắ"})


class PlannedFilenames(unittest.TestCase):
    def setUp(self):
        entries = [Entry(title, page) for page, title in enumerate(HOSTILE, start=1)]
        self.sections = plan(entries, len(entries))

    def test_every_filename_is_unique_ignoring_case(self):
        folded = [section.filename.casefold() for section in self.sections]
        self.assertEqual(len(folded), len(set(folded)))

    def test_every_destination_stays_inside_the_output_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            output = (Path(temp) / "out").resolve()
            for section in self.sections:
                with self.subTest(filename=section.filename):
                    self.assertEqual((output / section.filename).resolve().parent, output)
                    self.assertTrue(section.filename.endswith(".pdf"))
                    self.assertNotIn("/", section.filename)
                    self.assertNotIn("\\", section.filename)

    def test_the_suffix_keeps_the_whole_name_under_the_cap(self):
        for section in self.sections:
            with self.subTest(filename=section.filename):
                self.assertLessEqual(len(section.filename.encode("utf-8")), MAX_STEM_BYTES + len(" (99).pdf"))


if __name__ == "__main__":
    unittest.main()
