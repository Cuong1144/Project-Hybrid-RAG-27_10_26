# Kiểm tra OCR và dữ liệu nạp thêm

Dataset `dataset_cbcbf5ef45388863affb36cb` được tạo ngày 09/11/2026 từ thư mục `Tailieu`. OCR chạy local ở 300 DPI bằng Tesseract tích hợp trong MuPDF với hai model `vie` và `eng` đã khóa mã SHA-256.

| Hạng mục | Kết quả |
|---|---:|
| File nguồn ban đầu | 75 |
| RAR đọc được | 13 |
| PDF lấy ra từ RAR | 52 |
| Word có PDF tham chiếu | 25 |
| Trang trong các PDF tham chiếu Word | 158 |
| Trang chạy OCR | 107 |
| Trang OCR có văn bản chờ đối chiếu | 104 |
| Trang OCR có một đoạn đã đối chiếu | 1 |
| Trang OCR không nhận được chữ | 2 |
| Trang hoặc đoạn đủ điều kiện lập chỉ mục | 622 |
| PDF có nội dung đủ điều kiện lập chỉ mục | 74 |

So sánh trực quan trang PDF 4 của `clc-cntt.pdf` với bản OCR. Dòng `3. Mã ngành: 7.48.02.01 (IE3)` khớp với ảnh gốc và là phần duy nhất của trang được duyệt. Các bảng, số quyết định và ngày tháng còn lỗi nhận dạng hoặc chưa kiểm tra được giữ trong staging. Các bản tham chiếu Word đã được trích chữ để rà soát và cần xác nhận ánh xạ từng trang trước khi lập chỉ mục.

**105 bài kiểm thử và 15 ca con đạt.** Các ca mới kiểm tra OCR tiếng Việt trên nguồn thật, PDF xoay, toàn vẹn model, tách RAR an toàn, đối chiếu hai mã băm của Word và PDF tham chiếu, chặn ứng viên OCR chưa duyệt và chỉ lập chỉ mục các dòng đã duyệt. Hai bundle trước OCR vẫn vượt qua kiểm tra toàn vẹn.

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -s --basetemp .cache/pytest-ocr-release
```

## Kiểm tra bản demo sau phát hành

Hai bundle của dataset mới đã được phát hành ở API local. Fixed size có 1.584 chunk và Recursive có 1.663 chunk. Cả hai tra cứu đúng đoạn mã ngành IE3. Liên kết trả đúng bản PDF có SHA-256 khớp nguồn và mở trang 4. Gọi trực tiếp trang 1 chưa duyệt trả HTTP 404. Bundle trước OCR vẫn mở được theo ID cũ.

Qwen3 trên Recursive trả “3. Mã ngành: 7.48.02.01 (IE3)” cùng citation trang 4 trong 13,16 giây. Một job OCR qua worker thật nhận dạng 10 trang của cùng PDF ở chế độ chưa duyệt và không tạo trang đủ điều kiện lập chỉ mục. [Kết quả gọi API](live-smoke.json)
