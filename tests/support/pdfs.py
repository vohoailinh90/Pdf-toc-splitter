"""Build throwaway PDFs for the checks. Nothing here is committed as a binary.

Every page gets a distinct width (100 + its 0-based source index), so an
exported page can be traced back to the source page it came from by reading
nothing but its mediabox.
"""
import hashlib
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


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


def build_text_pdf(path, pages):
    """Write one page per entry of `pages`, each a list of text lines (empty list = blank page).

    Pages get the same distinct widths as `build_pdf`. Lines are drawn in the standard Helvetica
    font so `extract_text()` reads them back; ASCII only, since this is a text-layer fixture and
    not a font test.
    """
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }))
    for index, lines in enumerate(pages):
        page = writer.add_blank_page(width=100 + index, height=100)
        if not lines:
            continue
        drawing = ["BT", "/F1 8 Tf", "10 TL", "5 90 Td"]
        drawing += ["(%s) Tj T*" % line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                    for line in lines]
        drawing.append("ET")
        content = DecodedStreamObject()
        content.set_data("\n".join(drawing).encode("ascii"))
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        page[NameObject("/Contents")] = writer._add_object(content)
    with Path(path).open("wb") as stream:
        writer.write(stream)
    return Path(path)


def build_encrypted_pdf(path, password, pages=3):
    """A PDF whose user password is `password`. Needs `cryptography` (requirements-dev.txt)."""
    writer = PdfWriter()
    for index in range(pages):
        writer.add_blank_page(width=100 + index, height=100)
    writer.add_outline_item("Phần A", 0)
    writer.encrypt(password, algorithm="AES-128")
    with Path(path).open("wb") as stream:
        writer.write(stream)
    return Path(path)
