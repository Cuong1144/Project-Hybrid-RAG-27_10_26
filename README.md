# Cổng tra cứu thông tin đào tạo UTC bằng Hybrid RAG

Ứng dụng tiếng Việt tra cứu văn bản bằng BM25, FAISS và Reciprocal Rank Fusion (RRF). Chế độ hỏi đáp dùng Qwen3 qua Ollama trên máy local để chọn trích đoạn có dẫn nguồn tới đúng trang PDF.

```powershell
git clone https://github.com/Cuong1144/Project-Hybrid-RAG-27_10_26.git
Set-Location -LiteralPath 'Project-Hybrid-RAG-27_10_26\utc-hybrid-rag'
```

Làm theo [hướng dẫn cài đặt, nạp dữ liệu và chạy ứng dụng](utc-hybrid-rag/README.md). Sau khi clone, cung cấp bộ tài liệu nguồn trong thư mục `Tailieu` ở gốc repo hoặc đặt `UTC_SOURCE_ROOT` trỏ tới bộ tài liệu trên máy. Script cài đặt tạo `.env` riêng và tải model khi chạy `install-models.ps1`.

- [Kiến trúc tổng thể](output/he_thong_tong_the/Kien_truc_tong_the_Hybrid_RAG_UTC.md)
- [Đặc tả hệ thống](output/he_thong_tong_the/Dac_ta_toan_he_thong_Hybrid_RAG_UTC.md)
- [Biên bản kiểm thử](utc-hybrid-rag/reports/verification.md) và [kết quả đánh giá truy xuất](utc-hybrid-rag/reports/evaluation/analysis.md)
