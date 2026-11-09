# Biên bản kiểm thử UTC Hybrid RAG

Ngày 09/11/2026, kiểm tra mã nguồn và hai bundle đang được công bố trên Windows với Python 3.13. Kết quả dưới đây phân biệt kiểm thử phần mềm, kiểm tra dữ liệu đã lưu và phép đo truy xuất.

## Kiểm thử tự động

Chạy từ thư mục dự án bằng môi trường `.venv` đã cài.

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -s --basetemp .cache/pytest-completed-quotes
```

**84 test và 15 subtest đạt** trong 12,21 giây. Có hai cảnh báo deprecation từ plugin AnyIO của pytest và Starlette `TestClient` dùng `httpx`.

| Nhóm | Hành vi được kiểm tra |
|---|---|
| Nạp và chia chunk | Trùng byte, cùng tên khác nội dung, chạy lại, phạm vi metadata theo trang và lý do chặn Word/RAR/scan. Hai cách chia giữ ánh xạ ký tự về nguồn. |
| Bundle, phát hành và phục hồi | Checksum và tập ID, chiều vector, tham chiếu trang, bundle lỗi, xung đột khi phát hành, giữ bundle của truy vấn đang chạy, rollback và restore có kiểm tra nguồn. |
| Truy xuất và API | Lọc trước Top 8, RRF, phân biệt nhánh từ khóa rỗng với truy xuất quá hạn, trả `retrieval_incomplete` kèm trace. Kiểm tra bộ lọc, làm rõ phạm vi, thiếu căn cứ, lỗi model, nguồn đã công bố và quyền quản trị. |
| Hỏi đáp | Với tokenizer và Ollama giả lập, kiểm tra payload non-thinking, ngân sách context, schema JSON, anchor và câu trích nguyên văn. Từ chối trích đoạn làm mất phủ định hoặc ngoại lệ trong câu. |
| Đánh giá | Mẫu số Precision/Recall, ánh xạ đúng file/trang, loại trùng gold và kiểm tra cùng dataset/hợp đồng embedding. |

## Dữ liệu và phiên bản đã kiểm tra

Dataset `dataset_6cbe41d3f96365773f95279a` kiểm kê 75 file. Có 32 file PDF phục vụ với 359 trang, 41 file bị chặn và 2 lần xuất hiện trùng byte. Trong 386 trang PDF đã kiểm tra ở khâu nạp, 27 trang chưa đủ điều kiện đưa vào kho.

| Chiến lược | Bundle hiện hành theo SQLite | Chunk | Chiều vector |
|---|---|---:|---:|
| Fixed size | `b_8684ab9bfc21961408ae78d0` | 996 | 384 |
| Recursive | `b_d1303694380dd1af5cdec379` | 1.068 | 384 |

Chạy lại bộ xác minh trên cả hai bundle đã lưu. Checksum thành phần và nguồn khớp manifest, tập ID BM25/FAISS/chunk đồng nhất, vector hữu hạn và chuẩn hóa, span chunk khớp nội dung trang. SQLite ghi nhận cả hai lần phát hành và con trỏ hiện hành tương ứng. Đọc lại PDF snapshot của nguồn CNTT và đối chiếu cả ba trang. Văn bản raw khớp bản trích từ PDF, văn bản clean khớp sau chuẩn hóa NFC và khoảng trắng.

Phạm vi chương trình đã đối chiếu là `utc-cntt-cdr-828ac9132de4`, gắn với ba trang của tài liệu chuẩn đầu ra ngành Công nghệ thông tin. Đây là phạm vi nguồn để thử nghiệm hỏi đáp. Năm trong tên thư mục chưa xác định ngày ban hành, hiệu lực hay khóa áp dụng.

## Kết quả truy xuất

Phép đo ngày 09/11/2026 dùng cùng dataset, embedding và điều kiện truy xuất trên 15 câu có gold. Hai câu chẩn đoán được lưu riêng khỏi trung bình.

| Chỉ số trung bình | Fixed size | Recursive |
|---|---:|---:|
| Precision@3 | 40,00% | 53,33% |
| Recall@3 | 93,33% | 95,56% |
| EvidenceCoverage@3 | 90,00% | 96,67% |

Fixed có một đơn vị gold chưa được chunk nào bao phủ trọn vẹn. Số liệu là kết quả trên tập phát triển từ một PDF ba trang. Chi tiết từng câu và phân tích ranh giới chunk nằm trong [kết quả JSON](evaluation/retrieval_metrics.json), [bảng CSV](evaluation/retrieval_metrics.csv) và [phân tích](evaluation/analysis.md).

## Kiểm tra API và giao diện trên máy triển khai

- Tra cứu có bộ lọc CNTT qua API thật trả kết quả với cả Fixed size và Recursive. Yêu cầu hỏi đáp thiếu phạm vi trả `clarification_required`. Yêu cầu tổng tín chỉ trả `insufficient_evidence`.
- Worker xử lý job nạp lại qua API và tái sử dụng đúng dataset hiện có. Hai lần gửi cùng khóa idempotency trả cùng job. API quản trị thiếu token trả HTTP 401.
- Kiểm tra bằng Chrome ở 1.440 px và 390 px. Giao diện hiển thị 32 PDF, chuyển chiến lược, lọc thư viện và mở liên kết nguồn kèm số trang. PDF phản hồi HTTP 200, trang không hợp lệ trả HTTP 404. Hai kích thước kiểm tra không tràn ngang hay báo lỗi JavaScript.
- Đã tạo ZIP backup từ kho thật và phục hồi vào thư mục riêng. Kiểm tra lại cả hai bundle và chạy truy vấn sau phục hồi thành công. Hai con trỏ trong kho đang phục vụ được giữ nguyên.

## Chạy thử hỏi đáp với Qwen3 thật

Ollama 0.40.15 phục vụ Qwen3-4B Q4_K_M qua loopback với `OLLAMA_NO_CLOUD=1`. SHA-256 của file GGUF khớp trọng số chính chủ ở revision `bc640142c66e1fdd12af0bd68f40445458f3869b`. Cấu hình dùng non-thinking, cửa sổ 4.096 token và giới hạn đầu ra 512 token.

| Câu thử | Trang PDF trích dẫn | Token đầu vào Ollama báo | Thời gian API |
|---|---|---:|---:|
| Kỹ năng làm việc nhóm | 2 | 1.900 | 30,83 giây |
| Thời gian đào tạo | 1 | 1.815 | 7,27 giây |
| Thời gian đào tạo và kỹ năng làm việc nhóm | 2 và 1 | 1.875 | 18,79 giây |

Cả ba câu qua smoke test trên Recursive, trả `partial` với trích đoạn nguyên văn chứa nội dung cần đối chiếu. Mọi link đều trả HTTP 200. Đối chiếu từng trích đoạn với văn bản trên đúng trang PDF tải từ link. Bộ đếm trước khi gửi cao hơn số Ollama báo một token ở cả ba lượt, trong ngân sách đã đặt. Ca đầu gồm thời gian nạp thành phần ở tiến trình API mới. Ca làm việc nhóm còn một đoạn phụ về tinh thần học hỏi. Kiểm tra này xác nhận nguồn và nội dung cần tìm trong ba ca cụ thể. Chi tiết nằm trong [kết quả chạy thật](live_smoke.json).

Kiểm tra riêng trên giao diện với câu hỏi thời gian đào tạo. Chrome hiển thị “4 năm” trong 6,92 giây và liên kết `[E1]` mở PDF ở trang 1. Sau phép đo API, Ollama báo context 4.096 và khoảng 2,25 GiB VRAM cho model đã nạp. [Ảnh hỏi đáp trên giao diện](screenshots/utc-hoi-dap.png).

## Phần còn cần xác minh

- Các tài liệu Word cần PDF tham chiếu và ánh xạ trang. Các file RAR và trang scan cần giải nén hoặc OCR, rà soát từng trang và xác minh metadata trước khi công bố thêm. Kho chưa đủ căn cứ để tính tổng tín chỉ hoặc xác nhận toàn bộ học phần và hiệu lực văn bản.
- Đánh giá nghiệm thu cần tập kiểm thử độc lập, rà soát độ đầy đủ và độ đúng của câu trả lời, cùng đo tài nguyên theo tải triển khai.
