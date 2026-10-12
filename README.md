# PDF TOC Splitter

Tool Python tách PDF theo mục lục. Mỗi hạng mục tạo một PDF có tên là tiêu đề của hạng mục. Chạy cục bộ, không tải tài liệu lên dịch vụ ngoài.

## Cài đặt

Yêu cầu Python 3.10 trở lên. Mở terminal trong thư mục repo:

```bash
python -m venv .venv
# Linux / macOS:
source .venv/bin/activate
# Windows PowerShell:
# .venv\Scripts\Activate.ps1
pip install -e .
```

## Dùng ngay với mục lục trong ảnh

`examples/toc.txt` chứa đủ 32 mục từ ảnh. Thay `document.pdf` bằng đường dẫn PDF của bạn:

```bash
pdf-toc-splitter document.pdf --toc-file examples/toc.txt --dry-run
pdf-toc-splitter document.pdf --toc-file examples/toc.txt --output chapters
```

Ví dụ phần đầu lấy trang 8–11, phần tiếp theo lấy trang 12–13. Phần cuối lấy từ trang 75 tới hết PDF. Các trang trước phần đầu không được xuất.

## Chênh lệch số trang

Mọi số trang đều tính từ 1. Nếu mục lục ghi trang 8 nhưng nội dung thật ở trang thứ 10 của file PDF, thêm `--page-offset 2`:

```bash
pdf-toc-splitter document.pdf --toc-file examples/toc.txt --page-offset 2 --dry-run
```

Kiểm tra bằng `--dry-run`, sau đó bỏ tùy chọn này để xuất file. Offset có thể âm.

## Các nguồn mục lục khác

Bookmark sẵn trong PDF (mặc định lấy cấp 1):

```bash
pdf-toc-splitter document.pdf --output chapters
pdf-toc-splitter document.pdf --bookmark-level 2 --output subchapters
```

Bookmark đã trỏ đến trang vật lý, nên không dùng offset.

Mục lục dạng text ở trang vật lý 2–3 trong PDF:

```bash
pdf-toc-splitter document.pdf --toc-pages 2-3 --output chapters
```

Parser nhận mỗi dòng theo dạng `Tiêu đề....8`, `Tiêu đề 8` hoặc tiêu đề + tab + số trang. File mục lục UTF-8 hỗ trợ dòng trống và dòng chú thích bắt đầu bằng `#`. Nếu trang mục lục có tiêu đề, chân trang hoặc dòng bị xuống hàng, hãy sao chép và làm sạch thành file text rồi dùng `--toc-file`. PDF scan/ảnh không có lớp text cần OCR bên ngoài hoặc nhập mục lục thủ công; tool này không tự OCR.

PDF được bảo vệ bằng mật khẩu:

```bash
pdf-toc-splitter document.pdf --ask-password --toc-file examples/toc.txt
```

Nếu PDF dùng AES và báo thiếu dependency, cài `pip install 'pypdf[crypto]>=5,<7'`.

## Quy tắc xuất

- Tên file giữ Unicode và dấu tiếng Việt; ký tự không hợp lệ trên Windows được đổi thành `_`, tên quá dài được rút gọn.
- Các tiêu đề tạo cùng tên file được thêm `(2)`, `(3)`…
- Không ghi đè file đã tồn tại; chọn thư mục xuất mới nếu chạy lại.
- Số trang phải tăng nghiêm ngặt và nằm trong PDF. Hai hạng mục cùng bắt đầu trên một trang sẽ báo lỗi vì không thể tách riêng nội dung bằng ranh giới trang.
- Phần cuối kéo dài tới hết tài liệu, gồm cả phụ lục nếu có. Có thể thêm một mục phụ lục vào file mục lục để tách riêng.
- Tách ở cấp trang, giữ nội dung trang; không cam kết giữ nguyên mọi tính năng cấp tài liệu như chữ ký số hoặc biểu mẫu tương tác.

## Kiểm thử

```bash
python -m unittest discover -s tests -v
```

Các kiểm thử kiểm tra nội dung trang đầu/cuối bằng kích thước trang khác nhau, bookmark lồng nhau, độ lệch trang, tên trùng, trang sai và không ghi đè.

## Đưa lên GitHub

Repo Git đã được khởi tạo cục bộ. Nếu đã cài và đăng nhập GitHub CLI:

```bash
gh repo create pdf-toc-splitter --private --source=. --push
```
