import argparse
import getpass
import sys
from pathlib import Path
from pypdf import PdfReader
from .core import bookmark_entries, parse_toc, plan, split

def page_range(value):
    try:
        parts = value.split('-')
        if len(parts) > 2:
            raise ValueError()
        start, end = int(parts[0]), int(parts[-1])
        if start < 1 or end < start:
            raise ValueError()
        return start, end
    except ValueError:
        raise argparse.ArgumentTypeError('Dùng số trang 1-based, ví dụ: 2 hoặc 2-3')

def main():
    parser = argparse.ArgumentParser(description='Tách PDF theo mục lục; tên file là tên hạng mục.')
    parser.add_argument('pdf', type=Path)
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument('--toc-file', type=Path, help='Mục lục UTF-8: Tiêu đề....8')
    sources.add_argument('--toc-pages', type=page_range, help='Trang vật lý chứa mục lục, ví dụ 2-3')
    parser.add_argument('--bookmark-level', type=int, default=1)
    parser.add_argument('--page-offset', type=int, default=0, help='Trang PDF thực tế = số mục lục + offset (chỉ mục lục văn bản)')
    parser.add_argument('--output', type=Path, default=Path('split-output'))
    parser.add_argument('--dry-run', action='store_true', help='Xem trang và tên file, chưa ghi PDF')
    parser.add_argument('--ask-password', action='store_true')
    args = parser.parse_args()
    try:
        if args.bookmark_level < 1:
            raise ValueError('--bookmark-level phải >= 1')
        reader = PdfReader(args.pdf)
        if reader.is_encrypted:
            password = getpass.getpass('Mật khẩu PDF: ') if args.ask_password else ''
            if not reader.decrypt(password):
                raise ValueError('PDF có mật khẩu; chạy lại với --ask-password')
        if args.toc_file:
            entries = parse_toc(args.toc_file.read_text(encoding='utf-8-sig'))
        elif args.toc_pages:
            start, end = args.toc_pages
            if end > len(reader.pages):
                raise ValueError('Trang mục lục vượt quá số trang PDF')
            text = '\n'.join(reader.pages[i].extract_text() or '' for i in range(start-1, end))
            entries = parse_toc(text)
        else:
            if args.page_offset:
                raise ValueError('Bookmark dùng trang PDF thực tế; không áp dụng --page-offset')
            entries = bookmark_entries(reader, args.bookmark_level)
        sections = plan(entries, len(reader.pages), args.page_offset)
        for section in sections:
            print(f'{section.start:>4}-{section.end:<4} {section.filename}')
        if not args.dry_run:
            split(reader, sections, args.output, args.pdf)
            print(f'Đã tạo {len(sections)} file trong {args.output}')
        return 0
    except Exception as error:
        print(f'Lỗi: {error}', file=sys.stderr)
        return 1

if __name__ == '__main__':
    sys.exit(main())
