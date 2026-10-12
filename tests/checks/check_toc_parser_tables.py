"""check_toc_parser_tables - guards the toc-parser class and examples/*.txt.

parse_toc is a regex over text a human typed or a PDF text layer produced.
One row per accepted shape and one per rejected shape, so a change that
parses a new shape has to say what it does to the old ones.
"""
import unittest
from pathlib import Path

from pdf_toc_splitter.core import Entry, parse_toc, plan

ROOT = Path(__file__).resolve().parents[2]


class AcceptedShapes(unittest.TestCase):
    def test_each_line_shape(self):
        table = [
            ("Chương 1....8", ("Chương 1", 8)),                      # dot leader
            ("Chương 1 ........ 8", ("Chương 1", 8)),                # dot leader with spaces
            ("OAuth 2.0 12", ("OAuth 2.0", 12)),                     # plain space, digits inside the title
            ("Tiêu đề\t15", ("Tiêu đề", 15)),                        # tab
            ("Lời nói đầu … 7", ("Lời nói đầu", 7)),                 # ellipsis character
            ("Lời nói đầu…7", ("Lời nói đầu", 7)),
            ("  padded   ....  9  ", ("padded", 9)),                 # surrounding whitespace
            ("Top 5 common ways to improve API performance....71", ("Top 5 common ways to improve API performance", 71)),
            ("30 Free APIs for Developers....75", ("30 Free APIs for Developers", 75)),
            ("Chapter 1 8", ("Chapter 1", 8)),                       # the LAST number is the page
            ("Generative Adversarial Network (GAN) - AI by Hand ✍️....50", ("Generative Adversarial Network (GAN) - AI by Hand ✍️", 50)),
        ]
        for line, (title, page) in table:
            with self.subTest(line=line):
                self.assertEqual(parse_toc(line), [Entry(title, page)])

    def test_blank_lines_and_comments_are_ignored_and_order_is_kept(self):
        text = "# mục lục\n\nA....1\n   \n# ghi chú\nB....5\n"
        self.assertEqual(parse_toc(text), [Entry("A", 1), Entry("B", 5)])


class RejectedShapes(unittest.TestCase):
    def test_each_line_is_an_error(self):
        table = [
            ("no page at all", "không có trang"),
            ("title without page", "Chương 1...."),
            ("empty title", "....5"),
            ("trailing junk after the page", "Title 8x"),
            ("negative page", "Title -3"),
            ("decimal page", "Title 3.5"),
            ("empty text", ""),
            ("only comments and blanks", "# a\n\n# b\n"),
        ]
        for label, text in table:
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    parse_toc(text)

    def test_the_error_names_the_line(self):
        with self.assertRaises(ValueError) as caught:
            parse_toc("A....1\nB....2\nbad line\n")
        self.assertIn("3", str(caught.exception))


class ShippedExample(unittest.TestCase):
    """examples/toc.txt is what the README tells people to try first."""

    def setUp(self):
        self.entries = parse_toc((ROOT / "examples" / "toc.txt").read_text(encoding="utf-8"))

    def test_it_has_the_documented_shape(self):
        self.assertEqual(len(self.entries), 32)
        self.assertEqual((self.entries[0].page, self.entries[-1].page), (8, 75))
        self.assertTrue(all(entry.title for entry in self.entries))

    def test_pages_strictly_increase_so_it_can_be_planned(self):
        pages = [entry.page for entry in self.entries]
        self.assertEqual(pages, sorted(set(pages)))
        sections = plan(self.entries, 80)
        self.assertEqual((sections[0].start, sections[0].end), (8, 11))
        self.assertEqual((sections[-1].start, sections[-1].end), (75, 80))


if __name__ == "__main__":
    unittest.main()
