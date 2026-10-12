"""check_page_ranges - guards the page-planning class.

An off-by-one in plan() exports the wrong pages without raising anything, so
the boundaries are asserted as a table and the contiguity rule as a property.
"""
import random
import unittest

from pdf_toc_splitter.core import Entry, plan


def ranges(pages, count, offset=0):
    return [(s.start, s.end) for s in plan([Entry(f"t{i}", p) for i, p in enumerate(pages)], count, offset)]


class RangeTable(unittest.TestCase):
    def test_boundaries(self):
        table = [
            # (entry pages, page count, offset) -> expected (start, end) per section
            (([1], 5, 0), [(1, 5)]),
            (([3, 6], 10, 0), [(3, 5), (6, 10)]),              # pages before the first entry are not exported
            (([1, 10], 10, 0), [(1, 9), (10, 10)]),            # a last section of exactly one page
            (([1, 4], 10, 2), [(3, 5), (6, 10)]),              # positive offset
            (([5, 8], 10, -2), [(3, 5), (6, 10)]),             # negative offset
            (([1, 2, 3], 3, 0), [(1, 1), (2, 2), (3, 3)]),     # one page each
        ]
        for (pages, count, offset), expected in table:
            with self.subTest(pages=pages, count=count, offset=offset):
                self.assertEqual(ranges(pages, count, offset), expected)

    def test_rejected_inputs(self):
        table = [
            ("page 0", [0], 5, 0),
            ("past the end", [6], 5, 0),
            ("same page twice", [2, 2], 5, 0),
            ("decreasing", [3, 1], 5, 0),
            ("offset pushes below 1", [1], 5, -1),
            ("offset pushes past the end", [5], 5, 1),
        ]
        for label, pages, count, offset in table:
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    ranges(pages, count, offset)
        with self.assertRaises(ValueError):
            plan([], 5)

    def test_sections_tile_the_document_from_the_first_entry(self):
        rng = random.Random(20260101)
        for _ in range(300):
            count = rng.randint(1, 60)
            pages = sorted(rng.sample(range(1, count + 1), rng.randint(1, min(count, 12))))
            got = ranges(pages, count)
            self.assertEqual(got[0][0], pages[0])
            self.assertEqual(got[-1][1], count)
            for (_, end), (start, _) in zip(got, got[1:]):
                self.assertEqual(start, end + 1, f"gap or overlap for {pages} of {count}")
            self.assertTrue(all(s <= e for s, e in got))

    def test_the_offset_shifts_every_section_by_the_same_amount(self):
        base = ranges([2, 5, 9], 20)
        shifted = ranges([2, 5, 9], 20, 3)
        self.assertEqual([(s + 3, e + 3) for s, e in base[:-1]], shifted[:-1])
        self.assertEqual(shifted[-1], (base[-1][0] + 3, 20))


if __name__ == "__main__":
    unittest.main()
