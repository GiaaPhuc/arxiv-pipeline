# arxiv-pipeline — Chi tiết Cập nhật Nâng cấp Pipeline Thu thập Dữ liệu (v3.12.0)

> **Mục đích tài liệu:** Ghi nhận toàn diện các thay đổi mã nguồn, nguyên lý kiến trúc, thuật toán mới và hướng dẫn vận hành của bản nâng cấp **v3.12.0** cho crawler arXiv (Milestone 1). Bản nâng cấp tập trung vào khả năng chịu lỗi trích dẫn hai tầng, giải quyết bài toán thiếu lịch sử phiên bản và cung cấp bộ công cụ kiểm toán độc lập, bảo toàn 100% dữ liệu backup cũ.

---

## 1. Mục tiêu & Động lực Nâng cấp

Dựa trên thực tế vận hành thu thập 25.000 bài báo (~47.500 phiên bản) trên một máy:
1. **Nút thắt trích dẫn Semantic Scholar (S2):**
   * Rất nhiều bài báo mới (giai đoạn 2024–2025) chưa được Semantic Scholar lập chỉ mục (`HTTP 404`). Trước đây, các bài này chỉ nhận mảng trích dẫn rỗng `[]`, làm giảm mạnh độ phủ trích dẫn (*reference coverage*).
   * Lô truy vấn lớn (100 IDs) dễ gây lỗi `HTTP 413 Payload Too Large` hoặc `504 Gateway Timeout` khi trong lô có bài chứa hàng trăm trích dẫn mở rộng.
2. **Khoảng trống ngày tạo của các phiên bản sửa đổi trung gian:**
   * arXiv Atom API chỉ cung cấp ngày của bản $v_1$ (`published`) và bản mới nhất $v_N$ (`updated`). Các bản trung gian ($v_2..v_{N-1}$) nếu không lấy được ngày sẽ vi phạm ràng buộc kiểm tra của `validate_corpus.py`.
3. **Nhu cầu kiểm toán và vá lỗi độc lập:**
   * Cần có công cụ quét đĩa đối chiếu với SQLite manifest và khả năng làm giàu trích dẫn offline từ các tệp `.bib`/`.bbl` đã tải về mà không cần thực hiện lại các kết nối mạng tốn kém.

---

## 2. Chi Tiết Các Tệp Thay Đổi & Tính Năng Mới

### 2.1. Module Trích dẫn Hai Tầng & Thuật toán Chia đôi lô (`src/crawler/s2.py`)

* **Bộ bóc tách trích dẫn cục bộ (`extract_local_references`):**
  * Tự động quét đệ quy thư mục bài viết để tìm các tệp `.bib` và `.bbl` trong `tex/`.
  * Trích xuất các trường:
    * `title`: Làm sạch thẻ TeX và dấu ngoặc nhọn `{}`.
    * `author` / `authors`: Tách danh sách tác giả theo phân cách `and`, chuẩn hóa khoảng trắng.
    * `year` / `date`: Bóc tách năm xuất bản 4 chữ số.
    * `eprint` / `arxivid`: Nhận diện và chuẩn hóa mã định danh arXiv theo cả chuẩn mới (`YYMM.NNNNN`) và chuẩn cũ (`arch-ive/YYMMNNN`).
  * Khử trùng lặp trích dẫn cục bộ theo `ref_arxiv_id` hoặc tiêu đề chuẩn hóa.
* **Tối ưu hóa kích thước lô (`S2_BATCH_SIZE = 25`):**
  * Giảm kích thước lô mặc định từ 100 xuống 25. Vừa duy trì thông lượng cao, vừa loại bỏ nguy cơ quá tải payload phía máy chủ Semantic Scholar.
* **Thuật toán Chia đôi lô (Bisection):**
  * Khi `POST /graph/v1/paper/batch` trả về mã lỗi `413` hoặc các lỗi tạm thời `500, 502, 503, 504`:
    * Nếu lô có từ 2 bài trở lên, hệ thống tự động chia đôi danh sách ($N/2$) và gửi đệ quy 2 nửa.
    * Giúp cô lập các bài gây lỗi kích thước payload mà không làm gián đoạn hay thất bại toàn bộ các bài khác trong lô.
* **Cơ chế Fallback trích dẫn linh hoạt:**
  * Thêm tham số `paper_dirs: dict[str, Path] | None` và `enable_bib_fallback: bool` vào `fetch_references_batch` và `fetch_references`.
  * Khi S2 trả về `null` (404 - Not Indexed), nếu cờ fallback được bật và bài viết có tệp `.bib/.bbl`, hệ thống tự động bóc tách trích dẫn cục bộ, đánh dấu `source: "local_bib"`, `found: True`.

---

### 2.2. Khôi phục Lịch sử Phiên bản qua OAI-PMH (`src/crawler/discovery.py`)

* **Hàm `fetch_oai_revision_dates(arxiv_id)`:**
  * Khi arXiv Atom API không cung cấp đủ ngày cho các phiên bản trung gian, hàm này gửi truy vấn tới giao thức OAI-PMH:
    $$\text{https://oaipmh.arxiv.org/oai?verb=GetRecord\&metadataPrefix=arXivRaw\&identifier=oai:arXiv.org:}\{\text{arxiv\_id}\}$$
  * Bóc tách toàn bộ thẻ `<version>` và `<date>` chính thức, chuyển đổi thành định dạng ISO `YYYY-MM-DD`.
* **Tích hợp vào `fetch_revised_dates`:**
  * Khi cờ `config.enable_oai_dates` được kích hoạt, các bài nằm trong danh sách `incomplete` sẽ được bù đắp ngày tạo chính xác từ OAI-PMH cho các phiên bản từ $v_2 \dots v_N$, đảm bảo `len(paper.revised_dates) == paper.num_versions - 1` (do ngày của $v_1$ được lưu trữ tại `submission_date` theo hợp đồng dữ liệu).

---

### 2.3. Cấu hình Hệ thống & Giao diện Dòng lệnh (`src/crawler/config.py`, `src/run_crawl.py`)

* **Bổ sung các tham số cấu hình trong `Config`:**
  * `skip_s2: bool = False`: Bỏ qua các truy vấn Semantic Scholar trong luồng cào chính (ưu tiên tốc độ cào arXiv).
  * `enable_bib_fallback: bool = False`: Bật cơ chế dự phòng trích dẫn cục bộ.
  * `s2_batch_size: int = 25`: Kích thước lô truy vấn Semantic Scholar.
  * `enable_oai_dates: bool = False`: Bật cơ chế khôi phục ngày trung gian qua OAI-PMH.
* **Hỗ trợ biến môi trường tương ứng:**
  * `CRAWLER_SKIP_S2`
  * `CRAWLER_ENABLE_BIB_FALLBACK`
  * `CRAWLER_S2_BATCH_SIZE`
  * `CRAWLER_ENABLE_OAI_DATES`
* **CLI flags mới trong `run_crawl.py`:**
  * `--skip-s2`: Bỏ qua Semantic Scholar trong lượt chạy hiện tại (bù sau qua `--s2-only`).
  * `--enable-bib-fallback`: Kích hoạt trích dẫn từ file `.bib/.bbl` khi S2 404.
  * `--s2-batch-size N`: Tùy biến kích thước lô S2.
  * `--enable-oai-dates`: Kích hoạt bù ngày qua OAI-PMH.

---

### 2.4. Điều phối Luồng Pipeline (`src/crawler/pipeline.py`)

* **`_collect_s2_batch`:**
  * Nhận thêm tham số `max_batch_size=run.config.s2_batch_size`, gom lô động theo cấu hình thay vì hằng số cố định.
* **`_handle_s2_batch`:**
  * Tự động truyền ánh xạ `paper_dirs = {job.paper.arxiv_id: job.paper_dir for job in jobs}` và cờ `enable_bib_fallback` xuống `fetch_references_batch`.
  * Đồng bộ tốt với cả chế độ chạy thông thường và chế độ `--s2-only`.

---

### 2.5. Công cụ Độc lập Mới

#### A. `src/enrich_references.py` (Làm giàu trích dẫn offline)
* **Chức năng:** Quét toàn bộ kho dữ liệu `data/papers/`. Đối với các bài chưa có trích dẫn hoặc S2 chưa index, công cụ đọc tệp `.bib`/`.bbl` có sẵn và cập nhật thẳng vào `references.json` cùng tệp đánh dấu `s2_references.raw.json.gz`.
* **Ưu điểm:** Chạy offline 100%, tốc độ xử lý hàng ngàn bài chỉ trong vài giây, không tốn quota API.

#### B. `src/audit_corpus.py` (Kiểm toán tính toàn vẹn kho dữ liệu)
* **Chức năng:**
  * Quét đối chiếu toàn bộ tệp vật lý trên đĩa với các dòng trạng thái trong `manifest.sqlite`.
  * Xác minh mã băm SHA-256 của từng tệp `.tex`, `.bib`.
  * Thống kê số lượng phiên bản theo trạng thái, phát hiện các tệp rác ngoài luồng, phát hiện các bài thiếu trích dẫn và xuất báo cáo JSON.

---

## 3. Bảng So Sánh Trước và Sau Cập Nhật

| Tiêu chí | Trước cập nhật (`v3.11.1`) | Sau cập nhật (`v3.12.0`) |
| :--- | :--- | :--- |
| **Kích thước S2 Batch** | Cố định 100 bài/lô. | Mặc định 25 bài/lô, cấu hình linh hoạt qua CLI/env. |
| **Xử lý khi S2 quá tải (413/5xx)** | Thất bại cả batch, phải thử lại trong lượt chạy sau. | Tự động chia đôi lô (**Bisection**) và xử lý đệ quy. |
| **Hành vi khi S2 trả về 404** | Ghi nhận 0 trích dẫn (`found=False`), giảm độ phủ. | Tự động đọc `.bib`/`.bbl` cục bộ khi bật `--enable-bib-fallback`. |
| **Khôi phục ngày version trung gian** | Chỉ dựa vào arXiv Atom API, dễ thiếu ngày nếu API drop. | Tích hợp kênh fallback qua OAI-PMH `arXivRaw`. |
| **Công cụ làm giàu trích dẫn** | Bắt buộc phải chạy crawl lại qua mạng. | Có script `src/enrich_references.py` xử lý offline tức thì. |
| **Công cụ kiểm toán** | Chỉ có `validate_corpus.py` (pass/fail). | Bổ sung `src/audit_corpus.py` kiểm toán sâu và xuất JSON. |
| **Độ bao phủ Unit Test** | 269 passed. | **272 passed** (+3 test cho parser, fallback, bisection). |

---

## 4. Khả Năng Tương Thích Ngược với Dữ Liệu Backup Cũ

* **Cấu trúc Dữ liệu:**
  * Giữ nguyên `SCHEMA_VERSION = 1`.
  * Giữ nguyên định dạng thư mục `data/papers/<dirname>/v<N>/`.
  * Giữ nguyên cấu trúc bảng trong `manifest.sqlite`.
* **Tính tương thích:**
  * Toàn bộ dữ liệu cào trước đây **dùng lại được 100%**.
  * Tiến trình resume tự động nhận diện các bản ghi đã `ok` và bỏ qua, chỉ xử lý tiếp phần còn dở dang.

---

## 5. Hướng Dẫn Vận Hành

### 5.1. Tiếp tục chạy crawl trên dữ liệu backup cũ
```bash
python src/run_crawl.py --ranges src/id_ranges.csv --data-dir data --auto-resume-rate-limit --enable-bib-fallback
```

### 5.2. Làm giàu trích dẫn offline cho các bài đã cào trước đó
```bash
# Xem trước số lượng bài có thể làm giàu mà không ghi đĩa
python src/enrich_references.py --data-dir data --dry-run

# Thực hiện làm giàu trích dẫn chính thức
python src/enrich_references.py --data-dir data
```

### 5.3. Kiểm toán tính toàn vẹn kho dữ liệu
```bash
python src/audit_corpus.py --data-dir data --output data/stats/audit_report.json
```

---

## 6. Kết Quả Kiểm Thử Chất Lượng (Verification)

* **Bộ kiểm thử tự động (pytest):**
  ```bash
  pytest -q
  # Output: 272 passed in 30.52s
  ```
* **Kiểm tra cú pháp & phong cách mã nguồn (ruff):**
  ```bash
  ruff check .
  # Output: All checks passed!
  ruff format --check .
  # Output: 57 files already formatted
  ```
