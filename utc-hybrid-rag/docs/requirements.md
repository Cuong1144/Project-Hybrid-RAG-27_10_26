# Đối chiếu đặc tả và giới hạn xác minh

Nguồn yêu cầu là hai tài liệu `Dac_ta_toan_he_thong_Hybrid_RAG_UTC.docx` và `Kien_truc_tong_the_Hybrid_RAG_UTC.docx` trong `output/he_thong_tong_the`. Đặc tả toàn hệ thống phiên bản 1.1 ngày 08/11/2026 quy định baseline, các trạng thái nghiệp vụ và định nghĩa đánh giá. Tài liệu này là bản đồ để kiểm tra mã nguồn; các hàng dưới đây không phải biên bản xác nhận đã đạt.

| Yêu cầu | Thành phần chịu trách nhiệm | Bằng chứng cần kiểm tra |
|---|---|---|
| FR-01 danh mục và phạm vi | Catalog trong bundle, API, giao diện | Bộ lọc xuất phát từ bundle; phần chưa bao phủ hiển thị rõ |
| FR-02 tìm kiếm | BM25, FAISS, RRF, scope | Cùng tập ID hợp lệ; lọc trước Top 8; RRF hạng từ 1, hằng số 60 |
| FR-03 hỏi đáp | Điều phối, Ollama, hậu kiểm | Mỗi kết luận có anchor hợp lệ và được context hỗ trợ; đánh giá nghĩa độc lập |
| FR-04 mở nguồn | Evidence và nguồn snapshot/PDF | Đúng file, mục, trang PDF 1-based; nhiều trang được ánh xạ riêng |
| FR-05 làm rõ và giới hạn | Scope, API, giao diện | Phân biệt mơ hồ, thiếu căn cứ, kết quả một phần và lỗi kỹ thuật |
| FR-06 nạp dữ liệu | Pipeline 1, inventory, job, `utc_rag/ocr.py` và `utc_rag/archives.py` | Có kết quả hoặc lý do loại cho mỗi đầu vào. Mục RAR giữ xuất xứ. Trùng byte giữ source occurrence. Nạp lại cùng cấu hình cho cùng định danh |
| FR-07 xác minh | Metadata, hồ sơ OCR và QA | Duyệt đúng ứng viên bằng mã băm. Word cần ánh xạ trang đã đối chiếu. Trường dùng lọc cần `verified` và evidence |
| FR-08 phát hành/rollback | Bundle manager và con trỏ hiện hành | Kiểm checksum/ID/dimension trước đổi đồng bộ; query giữ bundle đã chọn |
| FR-09 nhật ký | Trace truy vấn, job và phát hành | Request/run ID, cấu hình, phiên bản, ranks, context và kết quả; không ghi secret |
| FR-10 đánh giá | `utc_rag/evaluation.py` | Cùng dataset/embedding; metric đúng mẫu số; lưu Top 3 riêng context |
| NFR-01/02 toàn vẹn và truy vết | Bundle, evidence, registry | Index → chunk → evidence → file/trang; citation kiểm nội dung, không chỉ URL |
| NFR-03 phục hồi | Job, release, backup/restore | Ngắt tiến trình, rerun, rollback và restore vào vị trí riêng |
| NFR-04 tài nguyên | Cấu hình, runtime, trace | Đo latency/RAM/VRAM trên máy thật; giới hạn query/context/timeout |
| NFR-05 quyền | API quản trị và nguồn | Thiếu quyền bị chặn; không cho đường dẫn tùy ý; chỉ dẫn trong nguồn là dữ liệu |
| NFR-06 tái lập | Manifest và evaluation | Ghi dataset/config/model/prompt/ground truth; không dùng cache sai phiên bản |

## Luồng kiểm thử tích hợp cần chạy

| Ca đặc tả | Kiểm tra trọng tâm |
|---|---|
| IT-01 | Trùng byte, cùng tên khác byte, chạy lại job |
| IT-02 | Bảng nhiều ngành/bậc, ô trống khác 0, metadata và khóa chưa biết |
| IT-03 | Word thiếu ánh xạ PDF bị chặn. Chữ OCR chờ duyệt, mã băm sai hoặc đoạn cắt dòng không vào chunk. Citation trỏ đúng trang PDF của bản nguồn hoặc bản tham chiếu |
| IT-04 | Bundle lỗi checksum, ID hoặc chiều vector không được nạp/phát hành |
| IT-05 | Query trong lúc publish, ngắt publish, rollback và restore |
| IT-06 | Filter hẹp, mã gần giống, so sánh hai phạm vi, làm rõ chương trình |
| IT-07 | RRF deduplicate đúng; retriever rỗng khác lỗi/timeout |
| IT-08 | Ngoài corpus, thiếu một ý, context thiếu chỗ, citation sai nghĩa |
| IT-09 | Admin thiếu quyền, nguồn/ID giả, chỉ dẫn độc hại trong văn bản |
| IT-10 | Chạy lại hai chiến lược, Top 3, evidence không đếm lặp, trace |
| IT-11 | Qwen local offline; dừng Ollama/thiếu bộ nhớ trả lỗi và không gọi cloud |

## Điều kiện đánh giá

Tập chính có ít nhất 5 câu keyword, 5 semantic và 5 synthesis; tập diagnostic được báo riêng. Ground truth dựa trên file/trang và đoạn nguồn, không dựa vào ID chunk. Mỗi evidence cần `file_id`, `pdf_page` và một khoảng ký tự nửa mở `[char_start, char_end)` hoặc `quote` trích nguyên văn. Ground truth phải được đối chiếu độc lập trước khi gọi là tập kiểm thử đã nghiệm thu.

Chunk được coi liên quan khi khớp file/trang và bao phủ toàn khoảng ký tự chuẩn hoặc chứa nguyên quote sau chuẩn Unicode NFC/khoảng trắng. Chunk nhiều trang cần text riêng từng source span để dùng phép khớp quote; không gán quote trong text ghép cho một trang bất kỳ. Các span trùng nhau chỉ được tính một đơn vị evidence.

Precision@3 luôn chia 3, kể cả khi trả ít hơn 3 kết quả. Recall@3 dùng số chunk liên quan của từng chiến lược; không tính Recall khi mẫu số bằng 0. EvidenceCoverage@3 dùng bằng chứng nguồn chung, không đếm tăng khi nhiều chunk overlap cùng bằng chứng. Evidence chưa ánh xạ được phải xuất rõ trong báo cáo, không bị loại âm thầm.

Module evaluation chỉ chấm retrieval; không tự xác nhận chất lượng câu trả lời, citation về nghĩa, trạng thái làm rõ hoặc việc OCR đúng. Các tiêu chí này cần dữ liệu gán nhãn và kiểm tra riêng. Report ghi số câu mỗi loại, tập phát triển/kiểm thử, phiên bản dataset, manifest của hai bundle và điều kiện tính.

Tập câu hỏi và kết quả trong `reports/evaluation/` thuộc dataset trước OCR `dataset_6cbe41d3f96365773f95279a`. Báo cáo giữ nguyên phiên bản đã đo. Đánh giá dataset mới cần ground truth tương ứng với nguồn và đoạn đủ điều kiện của hai bundle mới.

## Phạm vi baseline và phần cần đo trên máy thật

Baseline dùng hai chiến lược chunk, BM25/FAISS/RRF, API và giao diện tra cứu, nguồn có evidence, phiên bản và Ollama local. Pipeline đã có bước nạp tùy chọn cho OCR và RAR. Bản PDF từ Word được chuẩn bị riêng trong staging. File hoặc trang chưa xử lý được có lý do trong inventory và báo cáo độ phủ.

- **OCR PDF.** PyMuPDF với Tesseract tích hợp nhận dạng tiếng Việt và tiếng Anh từ ảnh toàn trang ở 300 DPI. Ứng viên lưu block và tọa độ trên PDF. Bộ nhận dạng hiện không cung cấp điểm tin cậy cho từng ký tự hay cấu trúc hàng cột đã xác minh. Khi đối chiếu, kiểm tra trực tiếp các dấu, chữ số, mã học phần, ngày và số văn bản trên ảnh nguồn.
- **Duyệt ứng viên.** `ocr-review` xuất văn bản, vị trí nguồn và mã băm ứng viên. `ocr_reviews` cho phép duyệt cả trang hoặc một đoạn liền gồm các dòng hoàn chỉnh sau khi đối chiếu. Phần được duyệt mới đi vào `pages` và chunk. Đổi bản OCR, PDF hoặc cấu hình nhận dạng cần xác minh lại ứng viên. Metadata phạm vi, hiệu lực và trường lọc có bằng chứng riêng.
- **Word và RAR.** PDF tham chiếu từ DOC/DOCX cần mã băm khớp Word gốc và PDF, cùng danh sách trang đã đối chiếu ánh xạ. Trang PDF tham chiếu cần OCR đi qua cả hai bước duyệt. Mục RAR đi qua bước kiểm tra tên, giới hạn, kích thước và checksum trước khi đọc như tài liệu nguồn. Evidence giữ quan hệ với RAR gốc.

Các lệnh nạp, xuất hồ sơ, duyệt và phát hành được mô tả trong [hướng dẫn OCR](../README.md#nạp-ocr-và-giải-nén-rar).

Kho bảng đào tạo đầy đủ, truy vấn học phần theo mẫu, tính tổng tín chỉ, reranker và query expansion là phần mở rộng có cổng dữ liệu/đánh giá riêng. Không khẳng định tổng tín chỉ hoặc danh sách học phần đầy đủ từ một tập đoạn truy xuất.

Các test tự động sử dụng fixture chỉ xác nhận hành vi phần mềm tương ứng. Chúng không chứng minh toàn corpus đã được xác minh, không thay thử Ollama/Qwen thật và không chứng minh throughput hoặc VRAM đủ. Việc tải đúng model/revision, non-thinking, tokenizer/template, ngân sách 4.096 token, suy luận offline, chất lượng parser/OCR và mở đúng trang trong browser cần biên bản kiểm trên môi trường triển khai.
