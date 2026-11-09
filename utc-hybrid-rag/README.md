# Cổng tra cứu thông tin đào tạo UTC bằng Hybrid RAG

Dự án chạy trên một máy, cung cấp giao diện tiếng Việt để tìm tài liệu và hỏi đáp có dẫn nguồn. Pipeline nạp lưu snapshot nguồn, đọc lớp chữ PDF và đưa các trang cần OCR vào khu vực rà soát. Sau khi xác minh, pipeline tạo hai bộ chunk Fixed size và Recursive rồi xây BM25 và FAISS. Pipeline tra cứu lọc cùng phạm vi cho hai nhánh, hợp nhất bằng RRF và giữ một bundle cố định trong mỗi yêu cầu.

Đây là bản triển khai baseline với phạm vi dữ liệu công bố một phần. Chức năng hỏi đáp dùng Qwen3-4B Q4_K_M qua Ollama local để chọn trích đoạn nguyên văn; backend kiểm tra anchor và nội dung trích dẫn. Bản hiện tại trả `partial` khi có trích đoạn hợp lệ, không chứng nhận một câu trả lời diễn giải tự do là đầy đủ hoặc đang áp dụng.

## Chạy nhanh

Dùng PowerShell 7 và Python 3.13 trên Windows. Chạy các lệnh dưới đây trong thư mục `utc-hybrid-rag`. Cài thư viện và tải model lần đầu cần Internet cùng đủ dung lượng đĩa. Khi tải đủ tài nguyên, ứng dụng suy luận local.

```powershell
.\setup.ps1
.\install-models.ps1
```

`setup.ps1` cài các phiên bản trong `requirements.txt` và tạo `.env` nếu chưa có, với token quản trị ngẫu nhiên. `install-models.ps1` tải embedding, tokenizer Qwen, Ollama portable và model Qwen3-4B Q4_K_M. Runtime portable mặc định gồm CPU/Vulkan; việc dùng GPU và tốc độ thực tế phụ thuộc máy. Script `scripts/download_ollama.py --full` hỗ trợ tải cả thư viện CUDA khi cần thử cấu hình khác.

Với bản clone mới, đặt tài liệu nguồn trong `Tailieu` cùng cấp với `utc-hybrid-rag` hoặc cấu hình `UTC_SOURCE_ROOT` trong `.env`. Nạp, xây và phát hành bundle theo [các bước bên dưới](#nạp-dữ-liệu-và-quản-lý-phiên-bản), hoặc khôi phục kho dữ liệu từ một bản backup sẵn có. Sau đó khởi chạy ứng dụng:

```powershell
.\start.ps1
```

Mở [giao diện tra cứu](http://127.0.0.1:8000) và [tài liệu API](http://127.0.0.1:8000/docs). Có thể đổi cổng bằng `.\start.ps1 -Port 8001`. `Ctrl+C` dừng API và worker do script khởi chạy. Ollama là tiến trình riêng. `/health` phân biệt trạng thái kho dữ liệu với trạng thái model local. Khi kho đã sẵn sàng, chế độ **Tìm tài liệu** có thể chạy độc lập với Ollama.

Nếu bộ nguồn có chuẩn đầu ra CNTT được nạp với metadata đã đối chiếu, chọn phạm vi đó trong **Hỏi đáp có nguồn** rồi thử câu “Thời gian đào tạo ngành Công nghệ thông tin là bao lâu?”. [Biên bản kiểm thử](reports/verification.md) ghi kết quả chạy thật và giới hạn dữ liệu.

## Dữ liệu và giới hạn

Đợt nạp OCR tạo dataset `dataset_cbcbf5ef45388863affb36cb` với **74 PDF và 622 trang hoặc đoạn đủ điều kiện tra cứu**. Đã lấy 52 PDF từ 13 tệp RAR và tạo 25 PDF tham chiếu từ Word. OCR chạy trên 107 trang. Một dòng mã ngành IE3 ở trang PDF 4 của `clc-cntt.pdf` đã đối chiếu và đủ điều kiện tra cứu. Có 104 trang OCR chờ đối chiếu, hai trang không nhận được chữ và 158 trang tham chiếu Word chờ kiểm tra ánh xạ. [Biên bản kiểm tra OCR](reports/ocr/verification.md)

Đợt chạy trước OCR dùng dataset `dataset_6cbe41d3f96365773f95279a`. Từ 75 file nguồn, 32 PDF có 359 trang đủ điều kiện tra cứu. Dataset này tạo 996 chunk Fixed size và 1.068 chunk Recursive. Đọc `coverage` trong manifest hoặc `GET /catalog` để xem độ phủ và lý do loại của bundle đang dùng.

Pipeline có OCR tiếng Việt và tiếng Anh, đọc tài liệu bên trong RAR và nạp bản PDF tham chiếu tạo từ Word. Ứng viên OCR và trang Word chờ đối chiếu nằm trong hồ sơ rà soát. Truy xuất dùng các trang hoặc đoạn đã qua điều kiện nạp tương ứng. Bảng chương trình, số quyết định, mã học phần, ngày tháng và tổng tín chỉ cần xác minh riêng trước khi dùng làm dữ liệu có cấu trúc.

Metadata dùng lọc đã được đối chiếu cho nguồn ba trang chuẩn đầu ra ngành Công nghệ thông tin:

```text
Tailieu\Chuan_dau_ra\Nam_2018\1_ CNTT_UTC_2018.pdf
program_version_id: utc-cntt-cdr-828ac9132de4
```

`program_version_id` là định danh nội bộ cho phạm vi nguồn. Tên thư mục `Nam_2018` không được dùng để xác định ngày ban hành, hiệu lực hay khóa áp dụng. Những trường chưa có bằng chứng được giữ ở trạng thái chưa xác minh. Các PDF khác có thể tìm văn bản, nhưng không được tự gán phạm vi đào tạo từ tên file hoặc thư mục.

Trong giao diện, chọn **Hỏi đáp có nguồn**, chọn phiên bản chương trình ở trên, rồi thử câu hỏi “Chuẩn đầu ra ngành Công nghệ thông tin yêu cầu những kỹ năng làm việc nhóm nào?”. Nếu chưa chọn phạm vi, API trả yêu cầu làm rõ. Luôn mở citation để đối chiếu đúng trang và điều kiện của nguồn; hệ thống không xác nhận văn bản đang có hiệu lực chỉ từ kết quả tìm kiếm.

## Cấu hình và thành phần

| Thành phần | Triển khai |
|---|---|
| Giao diện | HTML/CSS/JavaScript, không cần bước build frontend |
| API | FastAPI, Uvicorn; mặc định `127.0.0.1:8000` |
| Vận hành | SQLite cho job, con trỏ bundle và lịch sử phát hành |
| Trích xuất | PyMuPDF đọc lớp chữ PDF. Tesseract tích hợp nhận dạng trang cần OCR với model `vie` và `eng`. Evidence gắn với trang PDF nguồn hoặc PDF tham chiếu |
| Chunking | Fixed size và Recursive, 700 ký tự, overlap 100 ký tự |
| Từ khóa | BM25 với tokenizer Unicode giữ mã/số; Top 8 |
| Ngữ nghĩa | `intfloat/multilingual-e5-small`, CPU, vector chuẩn hóa 384 chiều; FAISS Top 8 |
| Hợp nhất | RRF `k=60`; lọc trước Top 8 ở cả hai nhánh |
| Sinh trích đoạn | `hf.co/Qwen/Qwen3-4B-GGUF:Q4_K_M`, Ollama loopback, non-thinking |
| Ngân sách sinh | Context 4.096 token, đầu ra tối đa 512 token, tối đa 5 chunk; tokenizer Qwen để tính ngân sách |

Các revision embedding/tokenizer được khóa trong `data/embedding_contract.json` và `data/generation_contract.json`. Không thay model hoặc thành phần index riêng lẻ bên trong bundle đã công bố.

`.env.example` mô tả biến môi trường. `UTC_SOURCE_ROOT` giới hạn nguồn mà API quản trị được phép nạp. Mặc định ứng dụng dùng `Tailieu` cùng cấp với `utc-hybrid-rag`. `UTC_DATA_DIR` có thể trỏ tới kho dữ liệu khác. `UTC_OLLAMA_URL` phải là địa chỉ HTTP loopback; `UTC_OLLAMA_MODEL` mặc định là model đã chọn trong đặc tả. `start.ps1` đặt `OLLAMA_NO_CLOUD=1` và `OLLAMA_NUM_PARALLEL=1`.

Token quản trị nằm trong `.env`, chỉ nhập vào mục **Quản trị dữ liệu** khi cần. Không gửi token vào câu hỏi, tài liệu nguồn hoặc nhật ký. Giao diện chỉ giữ token trong bộ nhớ trang. API tra cứu không yêu cầu token; các thao tác nạp, xây, phát hành và rollback dùng `Authorization: Bearer …`. Baseline dùng một quyền vận hành; chưa tích hợp hệ thống tài khoản/SSO nhiều vai trò.

```text
utc_rag/          API, ingest, OCR, rà soát, chunking, retrieval, generation, bundle, worker, evaluation, backup
web/              Giao diện tra cứu, thư viện nguồn và quản trị
config/           Metadata đã đối chiếu, phạm vi pilot, câu hỏi phát triển
data/snapshots/   File nguồn theo hash, không ghi đè bản cùng tên khác byte
data/datasets/    Văn bản chuẩn, evidence, registry và độ phủ theo phiên bản
data/bundles/     Chunk, BM25, FAISS, vector, ID map và manifest có checksum
data/staging/     Job nạp, ứng viên OCR, tài liệu giải nén và PDF tham chiếu chờ đối chiếu
data/traces/      Nhật ký yêu cầu, phiên bản, truy xuất, context và hậu kiểm
reports/          Kết quả kiểm tra và đánh giá đã chạy
.cache/           Model, tokenizer, file tải và log tiến trình local
.tools/           Ollama portable
```

## Nạp dữ liệu và quản lý phiên bản

Giao diện quản trị tạo job vào hàng đợi. Worker chạy riêng trong `start.ps1`; có thể chạy độc lập bằng lệnh sau. Chỉ chạy một worker cho một kho dữ liệu.

```powershell
.\.venv\Scripts\python.exe -m utc_rag worker
# Xử lý một lượt rồi thoát:
.\.venv\Scripts\python.exe -m utc_rag worker --once
```

Các lệnh CLI nạp/xây dưới đây chạy đồng bộ. File `config/metadata.json` chứa metadata đã đối chiếu và các trang làm bằng chứng; không chuyển trường sang `verified` khi chưa kiểm tra nguồn.

```powershell
.\.venv\Scripts\python.exe -m utc_rag ingest '..\Tailieu' --metadata 'config\metadata.json'
.\.venv\Scripts\python.exe -m utc_rag build '<dataset_id_tu_lenh_ingest>' --strategy both
.\.venv\Scripts\python.exe -m utc_rag versions
```

Thay `'<dataset_id_tu_lenh_ingest>'` bằng `dataset_id` do lệnh `ingest` trả về. Nếu dùng bộ tài liệu khác, cập nhật đường dẫn nguồn trong lệnh và chỉ dùng `--metadata` khi có metadata đã đối chiếu cho bộ đó. Cùng byte tại nhiều đường dẫn vẫn giữ các lần xuất hiện; nạp lại cùng đầu vào/cấu hình tạo cùng định danh. File chưa xử lý được phải có lý do trong độ phủ.

`build` chỉ tạo bundle đạt kiểm tra toàn vẹn, chưa tự công bố. Phát hành kiểm tra lại checksum, ID, vector, nguồn và smoke query trước khi đổi con trỏ. Mỗi chiến lược có con trỏ riêng. Dùng ID từ `versions`; `--expected-current` phải bằng bundle đang phục vụ chiến lược tương ứng. Lần đầu chưa có bundle hiện hành thì bỏ tham số này.

```powershell
# Công bố lần đầu cho hai chiến lược, dùng ID do build trả về.
.\.venv\Scripts\python.exe -m utc_rag publish '<bundle_id_fixed>'
.\.venv\Scripts\python.exe -m utc_rag publish '<bundle_id_recursive>'

# Thay phiên bản hiện hành; không bỏ kiểm tra xung đột.
.\.venv\Scripts\python.exe -m utc_rag publish '<bundle_id_moi>' --expected-current '<bundle_id_hien_hanh>'

# Quay lại bundle từng được phát hành của cùng chiến lược.
.\.venv\Scripts\python.exe -m utc_rag rollback '<bundle_id_cu>' --expected-current '<bundle_id_hien_hanh>'
```

Các chuỗi trong dấu `<…>` là chỗ cần thay, không chạy nguyên xi. Khi job bị ngắt, kiểm tra lỗi trước rồi dùng `python -m utc_rag resume-jobs` để đưa job `running`/`failed` về hàng đợi; worker kiểm tra và xử lý lại. Không sửa trực tiếp bundle hoặc snapshot đang được citation tham chiếu.

## Nạp OCR và giải nén RAR

Chạy lệnh `install` để tải hai model Tesseract đã khóa phiên bản về `.cache/tessdata`. Lệnh `check` kiểm SHA-256 của cả hai file. Các lượt OCR dùng model đã lưu và chạy local.

```powershell
.\.venv\Scripts\python.exe -m utc_rag.ocr install
.\.venv\Scripts\python.exe -m utc_rag.ocr check
.\.venv\Scripts\python.exe -m utc_rag ingest '..\Tailieu' --metadata 'config\metadata.json' --ocr --expand-archives
.\.venv\Scripts\python.exe -m utc_rag ocr-review '<dataset_id_tu_job>' --output 'reports\ocr-review'
```

`--ocr` nhận dạng ở 300 DPI khi trang thiếu lớp chữ nhưng có nội dung ảnh hoặc vector, trang có lớp chữ hỏng, hoặc trang hỗn hợp được đánh dấu cần OCR. Kết quả giữ chữ máy nhận dạng, các block có tọa độ PDF và mã băm ứng viên. Trang có lớp chữ đủ điều kiện tiếp tục dùng bản trích trực tiếp. `--expand-archives` cần UnRAR từ WinRAR hoặc trong `PATH`. Tài liệu PDF, DOC và DOCX được tách vào staging theo giới hạn của bộ nạp. Registry giữ tên mục, đường dẫn và mã băm RAR gốc. Tài liệu con giữ xuất xứ để đối chiếu, kể cả khi có nội dung trùng byte.

Trong **Quản trị dữ liệu** → **Nạp tài liệu**, bật **Nhận dạng trang quét bằng OCR** và **Đọc tài liệu trong tệp RAR** theo nhu cầu. Giao diện gửi hai cờ `ocr` và `expand_archives` cho `POST /admin/jobs`. Worker dùng cùng quy trình nạp như CLI. Ghi lại `dataset_id` từ kết quả job để xuất hồ sơ rà soát.

### Đối chiếu và duyệt trang OCR

Mở `index.md` trong thư mục kết quả `ocr-review` để đọc từng bản văn bản. `review.json` ghi đường dẫn PDF, số trang tính từ 1, trạng thái và `candidate_sha256`. Mở đúng trang PDF để kiểm tra chữ, số và bố cục. Với mục nằm trong RAR, dùng nguyên khóa `relative_path` ghi trong hồ sơ, gồm tên RAR và tên mục ngăn bằng `::`.

Tạo bản sao `config/metadata.json` trong `config/`, rồi thêm `ocr_reviews` bên dưới mục `files` tương ứng. Dùng chuỗi SHA-256 để duyệt cả trang, hoặc dùng đối tượng có `excerpt` để duyệt đúng một dải dòng liền nhau. Đoạn này phải giữ nguyên các dòng hoàn chỉnh trong ứng viên OCR sau chuẩn hóa khoảng trắng. Sao chép SHA-256 từ ứng viên đã đối chiếu.

```json
{
  "files": {
    "thu-muc/tai-lieu-quet.pdf": {
      "ocr_reviews": {
        "1": "<sha256-ung-vien-trang-1>",
        "4": {
          "candidate_sha256": "<sha256-ung-vien-trang-4>",
          "excerpt": "<dai-dong-hoan-chinh-da-doi-chieu>"
        }
      }
    }
  }
}
```

Các trường trong dấu `<…>` cần thay bằng dữ liệu thực. Với đoạn nhiều dòng, biểu diễn xuống dòng bằng `\n` trong chuỗi JSON. Chỉ duyệt những dòng đọc rõ từ ảnh nguồn. Khi số quyết định, ngày tháng, ký hiệu hoặc quan hệ hàng cột còn mơ hồ, giữ phần đó ở trạng thái chờ đối chiếu. Duyệt OCR xác nhận nội dung trang hoặc đoạn đã chọn. Phạm vi đào tạo, ngày hiệu lực và các trường dùng để lọc có bước xác minh metadata và bằng chứng riêng.

Chạy lại `ingest` với file metadata vừa cập nhật và các cờ đã dùng khi tạo ứng viên. Chạy `ocr-review` cho dataset mới để kiểm tra độ phủ, đúng trang và đúng đoạn đã duyệt trước khi `build`. Duyệt theo đoạn chỉ đưa đoạn đó vào chunk. Mã băm cũ hoặc đoạn cắt giữa dòng giữ trang ở trạng thái chờ đối chiếu. Có thể xem `raw_pages` để rà soát bản trích, còn `pages` là phần đủ điều kiện tạo chunk. Lệnh `publish` chạy truy vấn kiểm tra trước khi đổi con trỏ. Sau khi phát hành, thử tra cứu đoạn vừa duyệt và mở citation từ API hoặc giao diện để kiểm tra đích.

### Đưa DOC và DOCX vào quy trình rà soát

Nạp nguồn trước để có snapshot Word tại `data/snapshots/<sha256>/`. Trên Windows đã cài Microsoft Word, dùng PowerShell 7 với `-STA` để tạo PDF cố định bố cục trong `data/staging/`. Script nhận snapshot làm đầu vào, đối chiếu mã băm và tạo PDF ở đường dẫn mới.

```powershell
pwsh -NoProfile -STA -File '.\scripts\prepare-word-pdf.ps1' `
  -InputFile '.\data\snapshots\<sha256-word>\<ten-file>.docx' `
  -OutputPdf '.\data\staging\word-pdfs\<ten-file>.pdf'
```

Thêm `reference_pdf` vào mục Word trong `files` của metadata. Dùng `reference_path` do script trả về cho `path` và `reference_sha256` cho `sha256`. Đường dẫn `path` tính từ `data`. Khởi đầu với `reviewed_pages` rỗng. Sau khi đối chiếu nội dung, bố cục và ánh xạ với Word gốc, thêm số trang PDF đã xác minh vào mảng này.

```json
{
  "files": {
    "thu-muc/tai-lieu.docx": {
      "reference_pdf": {
        "path": "staging/word-pdfs/tai-lieu.pdf",
        "source_sha256": "<sha256-word>",
        "sha256": "<sha256-pdf-tham-chieu>",
        "reviewed_pages": []
      }
    }
  }
}
```

Chạy `ingest` lại với metadata mới. Pipeline lưu riêng Word gốc và PDF tham chiếu theo mã băm. Nếu một trang PDF tham chiếu cần OCR, trang đó phải qua cả bước đối chiếu ánh xạ Word và duyệt ứng viên OCR trước khi tạo chunk. Citation mở đúng trang của bản PDF tham chiếu đã đăng ký.

## API và trạng thái kết quả

| API | Chức năng |
|---|---|
| `GET /health` | Tiến trình sống, độ sẵn sàng kho và Ollama |
| `GET /catalog`, `GET /versions` | Phạm vi/bộ lọc đã xác minh và phiên bản đã công bố |
| `POST /search` | BM25 + FAISS + RRF, không gọi LLM |
| `POST /ask` | Phạm vi, truy xuất, chọn evidence, gọi Ollama và kiểm tra trích đoạn |
| `GET /sources/{file_id}?bundle_id=…&page=…` | PDF đã đăng ký thuộc đúng bundle và trang phục vụ |
| `GET/POST /admin/jobs`, `GET /admin/jobs/{job_id}` | Tạo và theo dõi job nạp. Có tùy chọn OCR và giải nén RAR |
| `POST /admin/build` | Đưa tác vụ xây bundle vào hàng đợi |
| `GET/POST /admin/releases`, `POST /admin/rollback` | Xem, phát hành và quay lại bundle |

Ví dụ body `/ask`:

```json
{
  "query": "Chuẩn đầu ra yêu cầu kỹ năng làm việc nhóm như thế nào?",
  "filters": {"program_version_id": "utc-cntt-cdr-828ac9132de4"},
  "strategy": "recursive"
}
```

Client có thể gửi `index_version` là bundle đã công bố tương thích với `strategy`. Response có `request_id`, bundle/dataset/index version, `citations`, các chunk đã truy xuất, evidence thực dùng, cảnh báo và phần thiếu. Liên kết nguồn được backend tạo từ registry; client và LLM không chọn đường dẫn đĩa tùy ý.

`clarification_required` yêu cầu chọn phạm vi. `insufficient_evidence` biểu thị chưa đủ bằng chứng. `partial` giữ phần trích dẫn có căn cứ. Nếu truy xuất vượt thời gian trước khi hai nhánh hoàn tất, hệ thống trả `retrieval_incomplete` cùng lý do `timeout` và ghi bước dừng vào trace. Lỗi model, nguồn hoặc toàn vẹn bundle trả trạng thái lỗi kỹ thuật. `answered` của `/search` biểu thị đã tìm được các đoạn để người dùng đối chiếu.

## Kiểm tra và đánh giá

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Bộ phát triển có 15 câu chia đều keyword, semantic và synthesis, cùng hai câu chẩn đoán. Gold giữ file/trang/khoảng ký tự/quote độc lập với chunk ID. Đánh giá yêu cầu hai chiến lược cùng dataset và hợp đồng embedding, xuất Precision@3, Recall@3 theo chunk và EvidenceCoverage@3 theo đơn vị nguồn, không đếm lặp overlap.

Kết quả dưới `reports/evaluation/` thuộc dataset trước OCR `dataset_6cbe41d3f96365773f95279a`. Bộ câu hỏi `config/evaluation_questions.json` ghi đúng phiên bản đó. Khi cả hai bundle hiện hành dùng dataset này, có thể chạy lại bằng `python -m utc_rag evaluate config/evaluation_questions.json --output reports/evaluation-rerun`. Dataset mới cần bộ ground truth được đối chiếu và gắn đúng phiên bản. Xem [ghi chú tập đánh giá](docs/evaluation_notes.md), [kết quả retrieval trước OCR](reports/evaluation/retrieval_metrics.json), [phân tích](reports/evaluation/analysis.md) và [đối chiếu đặc tả](docs/requirements.md).

## Backup và phục hồi

Backup lưu kho `data`, gồm snapshot, dataset, bundle, trace và bản sao SQLite nhất quán. Đặt file backup ngoài `data`; restore chỉ nhận thư mục mới hoặc rỗng, kiểm tra lại bundle và smoke query.

```powershell
.\.venv\Scripts\python.exe -m utc_rag backup 'backups\utc-data.zip'
.\.venv\Scripts\python.exe -m utc_rag restore 'backups\utc-data.zip' 'restored_data'
```

Lưu riêng mã nguồn, `config`, cấu hình triển khai và cache model khi chuyển máy; lệnh backup dữ liệu không đóng gói chúng. Bảo quản `.env` riêng như thông tin xác thực. Có thể đặt `UTC_DATA_DIR` trỏ tới thư mục đã restore trước khi khởi động API và worker.

## Tài liệu thiết kế đầu vào

Dự án đối chiếu bản 1.1 ngày 08/11/2026 của đặc tả và kiến trúc cùng hai bản Word tương ứng. Xác minh toàn bộ tài liệu, phục hồi bảng OCR theo hàng cột, hậu kiểm ngữ nghĩa câu diễn giải và nghiệm thu thực nghiệm là các phần còn cần kiểm chứng. [Đặc tả toàn hệ thống](../output/he_thong_tong_the/Dac_ta_toan_he_thong_Hybrid_RAG_UTC.md) · [Kiến trúc tổng thể](../output/he_thong_tong_the/Kien_truc_tong_the_Hybrid_RAG_UTC.md).
