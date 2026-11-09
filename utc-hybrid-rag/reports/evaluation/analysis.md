# Phân tích lần đánh giá retrieval

Kết quả lấy từ `retrieval_metrics.json` và các chunk thực trong hai bundle, lần chạy ngày 09/11/2026 lúc 00:22:57 UTC+7. Recursive có chỉ số trung bình cao hơn Fixed trong bộ phát triển này; chưa có bằng chứng để kết luận chiến lược tốt hơn trên toàn bộ corpus UTC hoặc đạt nghiệm thu.

| Chỉ số trung bình trên 15 câu có gold | Fixed size | Recursive |
|---|---:|---:|
| Precision@3 | 40,00% | 53,33% |
| Recall@3 theo chunk liên quan của từng chiến lược | 93,33% | 95,56% |
| EvidenceCoverage@3 theo bằng chứng nguồn | 90,00% | 96,67% |
| Số lần gold chưa được chunk nào bao phủ đầy đủ | 1 | 0 |

Các tỷ lệ là trung bình theo câu hỏi, không phải tỷ lệ gộp tất cả đơn vị evidence. Precision luôn chia cho 3. Recall dùng số chunk liên quan của từng chiến lược nên chịu ảnh hưởng của overlap; EvidenceCoverage đếm mỗi đơn vị nguồn một lần và phù hợp hơn khi đối chiếu mức bao phủ cùng căn cứ.

## Điều kiện so sánh

Hai bundle dùng cùng `dataset_6cbe41d3f96365773f95279a`, cùng bộ lọc chương trình `utc-cntt-cdr-828ac9132de4`, embedding CPU `intfloat/multilingual-e5-small` revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`, vector chuẩn hóa 384 chiều và inner product. Cả hai chia theo trang, kích thước tối đa 700 ký tự Unicode, overlap 100; BM25 Top 8, FAISS Top 8 và RRF hằng số 60. Fixed có 996 chunk toàn dataset, Recursive có 1.068 chunk. LLM không được gọi trong phép đo retrieval này.

Bộ câu hỏi gồm 5 keyword, 5 semantic, 5 synthesis và 2 diagnostic. Chỉ 15 câu có gold được tính vào trung bình. Gold được đối chiếu nội dung một PDF chuẩn đầu ra CNTT gồm ba trang, có nhãn `development` và `source_text_reviewed_by_developer`. Hai câu chẩn đoán không có gold; trường `expected_status` chưa được chấm trên API trong báo cáo này.

## Ca tốt SY02

Câu hỏi kết hợp kiến thức lập trình, thuật toán và vị trí kỹ sư phần mềm. Recursive trả ba chunk liên quan trong Top 3: trang 2 `[1118,1710)` chứa vị trí làm việc, trang 1 `[436,1043)` và `[943,1485)` đều chứa trọn chuẩn kiến thức lập trình tại `[946,1041)`. Precision@3, Recall@3 và EvidenceCoverage@3 đều bằng 1. Hai chunk trang 1 trùng cùng một đơn vị gold; chúng không tạo thêm bằng chứng độc lập. Fixed cũng bao phủ đủ hai đơn vị nguồn nhưng có một kết quả Top 3 không liên quan theo gold, nên Precision@3 bằng 2/3.

## Ca chưa đủ SY01

Câu hỏi yêu cầu cả mã ngành ở trang 1 và kỹ năng thuyết trình, giao tiếp, làm việc nhóm ở trang 2. Top 3 của cả hai chiến lược chỉ bao phủ đơn vị kỹ năng, bỏ mã ngành. EvidenceCoverage@3 đều bằng 0,5 dù các đơn vị gold đều có chunk chứa đầy đủ. Đây là thiếu bao phủ trong xếp hạng Top 3, không phải thiếu nội dung nguồn. Recursive có hai chunk overlap chứa cùng kỹ năng nên Precision@3 là 2/3, cao hơn Fixed 1/3; mức bao phủ hai ý của câu hỏi vẫn không tăng.

## Khác biệt do ranh giới chunk SY03

Gold chuẩn kiến thức mạng máy tính ở trang 1 có khoảng ký tự `[1181,1303)`. Fixed chia thành `[600,1300)` và `[1200,1485)`: chunk trước thiếu ba ký tự cuối, chunk sau thiếu phần đầu. Không chunk nào chứa trọn đơn vị này; báo cáo giữ `unmapped_evidence_indices=[0]`. Top 3 Fixed chỉ bao phủ gold vị trí tư vấn, quản trị hệ thống ở trang 2, nên EvidenceCoverage@3 là 0,5. Recall@3 vẫn bằng 1 vì mẫu số chỉ còn một chunk được ánh xạ là liên quan; không nên đọc chỉ số này thành đã đủ hai căn cứ.

Recursive giữ trọn gold mạng máy tính trong chunk trang 1 `[943,1485)`, xuất hiện ở hạng 3; hạng 2 chứa gold vị trí làm việc ở trang 2. Vì vậy EvidenceCoverage@3 tăng lên 1. Quan sát này giải thích trực tiếp một lợi ích của ranh giới theo đoạn trong lần chạy, nhưng không bảo đảm mọi điều kiện hoặc ngoại lệ dài đều được giữ nguyên.

## Giới hạn kết luận

Không sửa gold hoặc cấu hình chunking sau khi xem điểm để nâng kết quả của lần đo này. Chưa có tập kiểm thử độc lập, đánh giá thủ công chất lượng câu trả lời, hoặc ngưỡng chất lượng được chốt trước nghiệm thu. Điểm retrieval không chứng minh LLM trả lời đúng, đầy đủ hay áp dụng đúng hiệu lực; cũng không xác nhận tính đầy đủ của bảng chương trình, tổng tín chỉ hoặc tài liệu chưa phục vụ.
