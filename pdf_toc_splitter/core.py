"""Split PDF by bookmarks or a text table of contents (1-based pages)."""
from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata
from pypdf import PdfReader, PdfWriter

@dataclass(frozen=True)
class Entry:
    title: str
    page: int

@dataclass(frozen=True)
class Section:
    title: str
    start: int
    end: int
    filename: str

def parse_toc(text: str) -> list[Entry]:
    entries = []
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'(.+?)(?:\.{2,}|…+|\t+|\s+)\s*(\d+)\s*', line)
        if not match:
            raise ValueError(f'Dòng mục lục {number} không hợp lệ: {line}')
        title = match[1].strip().rstrip('.…').strip()
        if not title:
            raise ValueError(f'Dòng {number} thiếu tiêu đề')
        entries.append(Entry(title, int(match[2])))
    if not entries:
        raise ValueError('Không tìm thấy mục lục')
    return entries

def bookmark_entries(reader: PdfReader, level: int = 1) -> list[Entry]:
    entries = []
    def visit(items, depth):
        for item in items:
            if isinstance(item, list):
                visit(item, depth + 1)
            elif depth == level:
                page = reader.get_destination_page_number(item)
                if page is None or page < 0:
                    raise ValueError(f'Bookmark không có trang hợp lệ: {item.title}')
                entries.append(Entry(str(item.title), page + 1))
    visit(reader.outline, 1)
    return entries

def safe_name(title: str) -> str:
    title = unicodedata.normalize('NFC', title)
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', title)
    title = re.sub(r'\s+', ' ', title).strip(' .')
    title = title or 'Untitled'
    if title.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1,10)), *(f'LPT{i}' for i in range(1,10))}:
        title = '_' + title
    while len(title.encode('utf-8')) > 180:
        title = title[:-1]
    return title.rstrip(' .') or 'Untitled'

def plan(entries: list[Entry], page_count: int, offset: int = 0) -> list[Section]:
    if not entries:
        raise ValueError('Không tìm thấy mục lục; dùng --toc-file hoặc --toc-pages')
    starts = [item.page + offset for item in entries]
    if any(p < 1 or p > page_count for p in starts):
        raise ValueError(f'Trang bắt đầu phải nằm trong 1..{page_count}; kiểm tra --page-offset')
    if any(a >= b for a, b in zip(starts, starts[1:])):
        raise ValueError('Trang mục lục phải tăng nghiêm ngặt. Hai mục cùng trang không thể tách chính xác theo trang.')
    used, sections = set(), []
    for i, entry in enumerate(entries):
        base = safe_name(entry.title)
        name, n = base, 2
        while (name + '.pdf').casefold() in used:
            name = f'{base} ({n})'
            n += 1
        filename = name + '.pdf'
        used.add(filename.casefold())
        sections.append(Section(entry.title, starts[i], starts[i+1]-1 if i+1 < len(starts) else page_count, filename))
    return sections

def split(reader: PdfReader, sections: list[Section], output: Path, source: Path) -> None:
    # Validate all destinations before writing. Never overwrite existing files.
    for section in sections:
        destination = output / section.filename
        if destination.resolve() == source.resolve() or destination.exists() or destination.is_symlink():
            raise ValueError(f'File đã tồn tại hoặc trùng PDF gốc: {destination}')
    output.mkdir(parents=True, exist_ok=True)
    for section in sections:
        writer = PdfWriter()
        for index in range(section.start - 1, section.end):
            writer.add_page(reader.pages[index])
        writer.add_metadata({'/Title': section.title})
        with (output / section.filename).open('xb') as stream:
            writer.write(stream)
