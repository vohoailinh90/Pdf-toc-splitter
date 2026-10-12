"""check_split_roundtrip - guards output-write, bookmark-reader, page-planning, destructive-io.

The end-to-end contract, on PDFs built on the fly (no binary is committed):
every exported page is traceable to its source page, the exports tile the
document from the first entry, the source is never touched, and nothing that
already exists is ever overwritten. "The names came out right" is not enough:
the failure mode of this tool is the wrong pages in the right file.
"""
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader

from pdf_toc_splitter.core import Entry, bookmark_entries, plan, split
from tests.support.pdfs import build_pdf, sha256, tree, widths

OUTLINE = [("Phần A", 1, 1), ("Mục con", 2, 2), ("Phần B", 4, 1)]


class Roundtrip(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = build_pdf(self.root / "source.pdf", pages=6, outline=OUTLINE)
        self.reader = PdfReader(self.source)

    def export(self, entries, offset=0, output="out"):
        sections = plan(entries, len(self.reader.pages), offset)
        split(self.reader, sections, self.root / output, self.source)
        return sections

    def test_every_exported_page_comes_from_the_planned_source_page(self):
        sections = self.export([Entry("A", 2), Entry("B", 4), Entry("C", 5)])
        source = widths(self.source)
        for section in sections:
            with self.subTest(section=section.filename):
                self.assertEqual(widths(self.root / "out" / section.filename), source[section.start - 1:section.end])

    def test_exports_tile_the_document_from_the_first_entry_to_the_end(self):
        sections = self.export([Entry("A", 2), Entry("B", 4)])
        joined = [w for s in sections for w in widths(self.root / "out" / s.filename)]
        self.assertEqual(joined, widths(self.source)[1:])  # page 1 precedes the first entry, so it is not exported

    def test_page_offset_selects_the_shifted_pages(self):
        sections = self.export([Entry("A", 1), Entry("B", 3)], offset=1)
        self.assertEqual(widths(self.root / "out" / sections[0].filename), widths(self.source)[1:3])

    def test_bookmarks_drive_the_same_contract(self):
        self.assertEqual([e.title for e in bookmark_entries(self.reader)], ["Phần A", "Phần B"])
        self.assertEqual(bookmark_entries(self.reader, 2), [Entry("Mục con", 3)])
        sections = self.export(bookmark_entries(self.reader))
        self.assertEqual(widths(self.root / "out" / "Phần A.pdf"), [101, 102, 103])
        self.assertEqual(widths(self.root / "out" / "Phần B.pdf"), [104, 105])
        self.assertEqual(PdfReader(self.root / "out" / sections[0].filename).metadata.title, "Phần A")

    def test_the_source_is_never_modified(self):
        before = sha256(self.source)
        self.export([Entry("A", 1), Entry("B", 4)])
        self.assertEqual(sha256(self.source), before)


class NeverOverwrites(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = build_pdf(self.root / "source.pdf", pages=6)
        self.reader = PdfReader(self.source)
        self.sections = plan([Entry("A", 1), Entry("B", 4)], 6)

    def test_a_second_run_into_the_same_directory_is_refused_and_changes_nothing(self):
        split(self.reader, self.sections, self.root / "out", self.source)
        snapshot = tree(self.root / "out")
        with self.assertRaises(ValueError):
            split(self.reader, self.sections, self.root / "out", self.source)
        self.assertEqual(tree(self.root / "out"), snapshot)

    def test_one_existing_destination_blocks_the_whole_run_before_any_write(self):
        out = self.root / "out"
        out.mkdir()
        existing = out / self.sections[1].filename
        existing.write_bytes(b"precious")
        with self.assertRaises(ValueError):
            split(self.reader, self.sections, out, self.source)
        self.assertEqual(tree(out), {existing.name: sha256(existing)})
        self.assertEqual(existing.read_bytes(), b"precious")

    def test_the_source_pdf_cannot_be_an_output_destination(self):
        before = sha256(self.source)
        clash = plan([Entry("source", 1)], 6)
        with self.assertRaises(ValueError):
            split(self.reader, clash, self.source.parent, self.source)
        self.assertEqual(sha256(self.source), before)


if __name__ == "__main__":
    unittest.main()
