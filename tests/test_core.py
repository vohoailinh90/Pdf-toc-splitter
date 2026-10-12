import tempfile
import unittest
from pathlib import Path
from pypdf import PdfReader, PdfWriter
from pdf_toc_splitter.core import Entry, bookmark_entries, parse_toc, plan, safe_name, split

class SplitTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_toc('Chương 1....8\nOAuth 2.0 12'), [Entry('Chương 1', 8), Entry('OAuth 2.0', 12)])
        with self.assertRaises(ValueError):
            parse_toc('không có trang')

    def test_ranges_offset_and_names(self):
        result = plan([Entry('Tên?', 1), Entry('Tên:', 3)], 7, 1)
        self.assertEqual([(x.start, x.end, x.filename) for x in result], [(2,3,'Tên_.pdf'),(4,7,'Tên_ (2).pdf')])
        self.assertEqual(safe_name('../../CON'), '_.._CON')
        self.assertEqual(safe_name('CON'), '_CON')
        self.assertLessEqual(len(safe_name('ắ'*300).encode()), 180)

    def test_invalid_ranges(self):
        for entries in [[Entry('a',0)], [Entry('a',6)], [Entry('a',2),Entry('b',2)], [Entry('a',3),Entry('b',1)]]:
            with self.assertRaises(ValueError):
                plan(entries, 5)

    def test_end_to_end_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'source.pdf'
            writer = PdfWriter()
            for i in range(6):
                writer.add_blank_page(width=100+i, height=100)
            parent = writer.add_outline_item('Phần A', 1)
            writer.add_outline_item('Mục con', 2, parent=parent)
            writer.add_outline_item('Phần B', 4)
            writer.write(source)
            reader = PdfReader(source)
            self.assertEqual(bookmark_entries(reader, 2), [Entry('Mục con',3)])
            sections = plan(bookmark_entries(reader), len(reader.pages))
            split(reader, sections, root/'out', source)
            a, b = PdfReader(root/'out/Phần A.pdf'), PdfReader(root/'out/Phần B.pdf')
            self.assertEqual([float(p.mediabox.width) for p in a.pages], [101,102,103])
            self.assertEqual([float(p.mediabox.width) for p in b.pages], [104,105])
            self.assertEqual(a.metadata.title, 'Phần A')
            with self.assertRaises(ValueError):
                split(reader, sections, root/'out', source)

    def test_image_toc(self):
        entries = parse_toc((Path(__file__).parents[1]/'examples/toc.txt').read_text())
        sections = plan(entries, 80)
        self.assertEqual(len(sections), 32)
        self.assertEqual((sections[0].start,sections[0].end), (8,11))
        self.assertEqual((sections[-1].start,sections[-1].end), (75,80))

if __name__ == '__main__':
    unittest.main()
