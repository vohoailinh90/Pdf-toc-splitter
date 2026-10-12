"""Build throwaway PDFs for the checks. Nothing here is committed as a binary.

Every page gets a distinct width (100 + its 0-based source index), so an
exported page can be traced back to the source page it came from by reading
nothing but its mediabox.
"""
import hashlib
from pathlib import Path

from pypdf import PdfReader, PdfWriter


def build_pdf(path, pages=6, outline=()):
    """Write `pages` blank pages; `outline` is (title, page_index, level) rows.

    `level` 1 is a top-level bookmark; a deeper level nests under the most
    recent bookmark one level up.
    """
    writer = PdfWriter()
    for index in range(pages):
        writer.add_blank_page(width=100 + index, height=100)
    parents = {}
    for title, page_index, level in outline:
        parents[level] = writer.add_outline_item(title, page_index, parent=parents.get(level - 1))
    with Path(path).open("wb") as stream:
        writer.write(stream)
    return Path(path)


def widths(path):
    return [int(float(page.mediabox.width)) for page in PdfReader(path).pages]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree(directory):
    """Every file under `directory` with its digest - a snapshot to compare."""
    root = Path(directory)
    return {str(p.relative_to(root)): sha256(p) for p in sorted(root.rglob("*")) if p.is_file()}
