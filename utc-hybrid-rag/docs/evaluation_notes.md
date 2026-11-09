# Bộ câu hỏi đánh giá phát triển

`config/evaluation_questions.json` gồm 15 câu có evidence chuẩn: 5 câu khớp từ khóa, 5 câu diễn đạt lại và 5 câu tổng hợp. Hai câu chẩn đoán bổ sung kiểm tra câu ngoài nguồn và yêu cầu chưa chọn phạm vi chương trình.

Nguồn là PDF `Chuan_dau_ra/Nam_2018/1_ CNTT_UTC_2018.pdf`, gồm ba trang chuẩn đầu ra ngành Công nghệ thông tin. ID file và dataset được ghi trong JSON. Tên thư mục `Nam_2018` không được dùng để suy ra ngày ban hành, hiệu lực hoặc khóa áp dụng. Các câu nghiệp vụ đều chọn `program_version_id` đã xác minh trong `config/pilot.json`.

Evidence chuẩn là toàn bộ một đoạn trong `clean_text`, dưới 150 ký tự, có `file_id`, trang PDF bắt đầu từ 1, khoảng ký tự nửa mở `[char_start, char_end)` và quote nguyên văn. Năm câu tổng hợp đều yêu cầu hai đơn vị evidence khác nhau ở hai trang. Đã kiểm tra các khoảng ký tự và quote khớp bản nguồn trong dataset; việc tạo gold không dựa vào kết quả xếp hạng retrieval.

Bộ này có nhãn `split=development` và `ground_truth_review_status=source_text_reviewed_by_developer`. Phạm vi chỉ gồm một tài liệu ngắn và có dùng lại một số evidence giữa các câu. Đây chưa phải tập kiểm thử độc lập hoặc biên bản nghiệm thu chất lượng toàn bộ corpus UTC. Cần bổ sung tập kiểm thử do người khác đối chiếu nguồn, câu có điều kiện/ngoại lệ, nhiều chương trình và tài liệu mới trước nghiệm thu.

Chạy so sánh khi cả hai bundle đã công bố trên cùng dataset và cùng hợp đồng embedding:

```powershell
.\.venv\Scripts\python.exe -m utc_rag evaluate config\evaluation_questions.json --output reports\evaluation
```

Module xuất Precision@3, Recall@3 theo chunk liên quan của từng chiến lược và EvidenceCoverage@3 theo đơn vị nguồn dùng chung. Mỗi evidence overlap chỉ được đếm một lần. Nếu evidence chưa được chunk nào bao phủ đầy đủ, báo cáo giữ danh sách chưa ánh xạ để xem xét; không tự nới gold hoặc đổi mẫu số nhằm làm tăng điểm.

Module chỉ đánh giá retrieval, không kiểm tra chất lượng câu trả lời hoặc status của API. Hai trường `expected_status` trong câu chẩn đoán là tiêu chí để kiểm riêng `/ask`; chúng không được dùng như kết quả đã đạt. Không có gold cho học phí năm 2026, tổng tín chỉ, danh sách học phần đầy đủ hoặc hiệu lực hiện hành.
