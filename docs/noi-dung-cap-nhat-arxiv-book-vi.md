# Hướng dẫn Cập nhật Báo cáo Jupyter Book (`arxiv-book`) — Đồng bộ Crawler `v3.18` & Phản hồi QA của Giảng viên

> **Mục đích tài liệu:** Tổng hợp đầy đủ, chi tiết theo **từng chương, từng mục** những nội dung cần cập nhật trên quyển báo cáo [`giaaphuc.github.io/arxiv-book`](https://giaaphuc.github.io/arxiv-book/) sau khi đợt crawl lại toàn bộ 25.000 ID hoàn tất.
>
> **Cơ sở đối chiếu:**
> 1. Bản Jupyter Book đang chạy thực tế trên GitHub Pages (hiện đang mô tả kiến trúc **`v3.14`**, bộ test **`765`**, S2 lô **`25`**, và hiệu năng cũ **`67,30 giờ`** / `2.592` lỗi S2 429).
> 2. Mã nguồn hiện tại trên nhánh tối ưu **`v3.18`** (`src/crawler/`, `src/parsing/`, `src/matching/` — **`816` unit tests** pass).
> 3. Toàn bộ **26 phản hồi chính thức của Giảng viên** trong file `[IntroDS] QA Replies - 2627-1 - Milestone 1.csv` (ký hiệu **`[QA #k]`** bên dưới).

---

## Mục lục

1. [Bảng Đối chiếu Tổng quan: Bản Book Cũ (`v3.14`) vs Bản Cập nhật Mới (`v3.18` + QA)](#1-bảng-đối-chiếu-tổng-quan-bản-book-cũ-v314-vs-bản-cập-nhật-mới-v318--qa)
2. [Cấu trúc Mục lục (`_toc.yml`) & Quy định Chung của Book](#2-cấu-trúc-mục-lục-_tocyml--quy-định-chung-của-book)
3. [Cập nhật Chi tiết Phần Milestone 1 (`m1/01` → `m1/04`)](#3-cập-nhật-chi-tiết-phần-milestone-1-m101--m104)
   - [Chương 1 — `m1/01_overview` (Tổng quan & Phạm vi)](#31-chương-1--m101_overview-tổng-quan--phạm-vi-thu-thập-dữ-liệu)
   - [Chương 2 — `m1/02_implementation` (Triển khai & Công cụ)](#32-chương-2--m102_implementation-quá-trình-triển-khai--công-cụ-sử-dụng)
   - [Chương 3 — `m1/03_data_statistics` (Thống kê Dữ liệu)](#33-chương-3--m103_data_statistics-thống-kê--phân-tích-dữ-liệu-thu-thập)
   - [Chương 4 — `m1/04_performance` (Đánh giá Hiệu năng)](#34-chương-4--m104_performance-đánh-giá-hiệu-năng-bộ-thu-thập)
4. [Cập nhật & Rà soát Phần Milestone 2 (`m2/01` → `m2/04`) & Milestone 3 (`m3/01` → `m3/04`)](#4-cập-nhật--rà-soát-phần-milestone-2-m201--m204--milestone-3-m301--m304)
5. [Checklist Thực thi Sau Khi Crawl Xong (Trước khi Build & Deploy Pages)](#5-checklist-thực-thi-sau-khi-crawl-xong-trước-khi-build--deploy-pages)

---

## 1. Bảng Đối chiếu Tổng quan: Bản Book Cũ (`v3.14`) vs Bản Cập nhật Mới (`v3.18` + QA)

| Hạng mục | Đang viết trên Book cũ (`v3.14`) | Cần cập nhật theo Code `v3.18` & QA Giảng viên | Căn cứ |
| :--- | :--- | :--- | :--- |
| **Cấu trúc `_toc.yml`** | `m1/01_overview` là `root` nằm **ngoài** Part 1 (`Milestone 1`), làm Part 1 bắt đầu từ Chương 2; gồm 3 Part (M1, M2, M3 — 12 chương) | Đưa `01_overview` vào đúng cấu trúc của Milestone 1 (tạo `intro.md` mỏng làm trang bìa chứa thông tin dự án + bảng thành viên), đảm bảo 2 phần cốt lõi **Milestone 1** và **Milestone 2** tách bạch rõ ràng (Milestone 3 đánh dấu là Phần mở rộng / Phụ lục hoàn chỉnh chuỗi dự án) | **[QA #6, #16.2, #21]** |
| **Tên thành viên** | Đã có đủ họ tên 5 thành viên + vai A–E ở mục `1.1.1`, không ghi MSSV | **Giữ nguyên** (đã đúng chuẩn bảo mật thông tin và yêu cầu của thầy); nếu tách `intro.md` thì đưa bảng này ra trang bìa | **[QA #21]** |
| **Phiên bản kiến trúc M1** | `arxiv-pipeline v3.14` | **`arxiv-pipeline v3.18`** (hợp nhất `v3.15` Start-to-Start, `v3.16` HTTP/2 + Header Early-Reject + Cut-Through `O(1)` RAM + CDN Telemetry, `v3.17.1` Traffic-Shaping & Leaky Byte-Budget, `v3.18` S2 Batch 150 + Save-First + AIMD) | `src/crawler/*` |
| **Nhịp chờ arXiv (`RateLimiter`)** | Start-to-Start `3.15 s + jitter (0..0.20 s)` **kèm** sàn End-to-Start cứng `arxiv_min_idle_gap = 3.05 s` | **Start-to-Start chuẩn `3.05 s + jitter (0..0.10 s)`** (`>= 3.00 s`) + đệm hậu tải nhẹ **`arxiv_min_idle_gap = 0.25 s`**, kết hợp **Deficit Breather + Defensive Soft-Pacing (`3.80–4.50 s`)** khi mạng/CDN nghẽn. Giải thích rõ lý do thiết kế cơ chế lai (hybrid) cho cả gói nhỏ lẫn gói nặng | `http.py`, **[QA #26]** |
| **Giao thức HTTP & Keep-Alive** | Chỉ HTTP/1.1 `_SingleConnAdapter`; xoay session mỗi `120` req, gói `>= 30 MiB`, hoặc `TTFB >= 3.5 s` | Hỗ trợ **HTTP/2 kết nối đơn** (`--http2` / `httpx[http2]`, `max_connections=1`, fallback HTTP/1.1); giữ ấm Keep-Alive mỗi **`500` req**, chỉ xoay khi gói **`>= 50 MiB`**, `TTFB >= 5.0 s`, đứt luồng/429 hoặc vượt ngân sách byte | `http.py`, `pipeline.py` |
| **Ngắt sớm PDF / ZIP (`no_source`)** | Đọc `512 bytes` đầu để xét magic `%PDF` / `PK` | **Zero-Body `Content-Type` Header Early-Reject (`v3.16`)**: Ngắt ngay từ HTTP Header (`application/pdf`, `application/zip`) với **`wire_bytes = 0`** (phát `RST_STREAM` trên HTTP/2 không đóng TCP); chỉ dùng `512 B` magic làm lớp dự phòng | `source.py` |
| **Điều tiết băng thông & Bài nặng** | `--max-stream-mbps` `4.5` (local) / `12–18 MiB/s` (Cloud); breather `S / 4.0 MiB/s` (trần `12 s`); **cứ đủ `50 MiB` ngủ cứng `12 s`** | Mặc định mượt **`6.0 MiB/s`**; **gỡ bỏ hoàn toàn van ngủ cứng `50 MiB -> 12 s`**; **Deficit Breather** trừ thời gian đã tải `(TTFB + download_seconds)` (trần `6.0 s`); thêm **Traffic-Shaping Interleaver** (tỷ lệ vàng chunk `>=16` + xen kẽ `v2..vN` bài `>=25 MiB` cách `15` bản nhẹ) & **Leaky Byte-Budget `360 MiB / 180 s`**; tự hồi phục khi Fastly `HIT` thuần `< 3.0` / `< 2.0 MiB/s` | `pipeline.py`, `preflight.py`, **[QA #13, #17, #22.1]** |
| **Giải nén gói nguồn** | Đệm `bytearray <= 96 MiB` trong RAM đẩy qua `extract_queue` (`192 MiB`) | Bổ sung **Cut-Through In-Flight Extraction `O(1)` RAM (`--in-flight-extract`)** giải nén trực tiếp trên luồng socket (đệm `128 KiB`, `0 B` `.tmp/`), tự lùi về RAM/`HTTP 206 Range` ở lượt vét deferred | `source.py`, `pipeline.py` |
| **Semantic Scholar (`s2.py`)** | Lô `25` ID (`45 s` gom); Bisection `413/5xx`; gọi `GET /references` ngay khi `referenceCount > len(refs)`; thang chờ 429 dài (gây `2.592` lần 429 / `23,26 h` ở bản cũ) | **Lô mật độ cao `150` ID** (giảm `6×` số request); Bisection cả `HTTP 400` size-limit / `>= 9.500` refs; **diệt false-positive `/references`** (chỉ gọi khi `len==0`, `>=500`, hoặc `<50%` với `refCount>=10`); **Save-First Isolation** lưu ngay bài xong; **Fast S2 Backoff (`2→4→8→15 s`) + AIMD** & `2` vòng vét deferred trong `--s2-only` | `s2.py`, `pipeline.py` |
| **Xử lý bài S2 trả `HTTP 404`** | Đang gộp các bài fallback `.bib/.bbl` (`local_bib`) vào chung `s2_state = "found"` (`99.96%`, chỉ `11` bài `not_indexed`) | **Tách bạch rõ số bài S2 trả `HTTP 404` (`not_indexed`)** và giải thích nguyên nhân (bài mới chưa lập chỉ mục), không để fallback `.bib` làm sai lệch tỷ lệ thành công thực của S2 API | **[QA #23.1]** |
| **Version trả `HTTP 404` trên `/src/`** | Thống kê `121` cặp `not_found (0.29%)` nhưng chưa giải thích trực tiếp theo quy ước bản ghi rỗng của thầy | Giải thích rõ: các version có trong metadata nhưng `/src/<id>v<n>` trả `404` vẫn được **lưu bản ghi rỗng `v<n>/meta.json`** (như bản chỉ có PDF), đánh dấu `status="not_found"` kèm lỗi `404`, và **không tính là thành công** | **[QA #23.2]** |
| **Timestamp crawl & Kaggle Snapshot** | Nêu dùng `--snapshot` nhưng chưa ghi ngày phát hành snapshot đối chiếu với ngày crawl | Ghi rõ **ngày phát hành bản Kaggle Snapshot** + **khoảng thời gian/ngày thực tế của các phiên crawl & checkpoint** để đối chiếu số lượng version tại thời điểm đó | **[QA #23.3]** |
| **Đo Runtime & Log đa phiên Colab** | Chương 4 chỉ có bảng tổng hợp chung (`67,30 h`), nhắc công thức `max(T_arXiv, T_S2)`, chưa có bảng log từng phiên | (1) Đo trên **Google Colab CPU-only** trọn bộ **25.000 ID**; (2) Tổng runtime **tính luôn mọi retry/delay/cooldown**; (3) Nếu chạy 2 giai đoạn tách rời (`--skip-s2` rồi `--s2-only`) thì tổng wall time là **tổng tuần tự các phiên (`sum`), KHÔNG lấy `max`**; (4) Thêm **Bảng Đối chiếu Nhật ký Từng Phiên Colab** | **[QA #9, #12, #13, #15, #16.1]** |
| **`.bbl` & References viết tay** | M2 mục `6.4` đã nêu khôi phục `.bbl`, chưa bàn sâu trường hợp `\section{References}` gõ tay không có `.bib`/`\bibitem` | Nhấn mạnh chuỗi giữ `.bbl` ở M1 -> chuyển ngược sang `BibtexRow` ở M2 (**[QA #18]**), và bổ sung mục phân tích/xử lý trường hợp tác giả viết tay References không dùng `.bib`/`\bibitem` (**[QA #25]**) | **[QA #18, #25]** |
| **Tổng số Unit Tests** | `765` tests (`278` M1 + `324` M2 + `163` M3) | **`816` tests** (`319` M1 + `334` M2 + `163` M3 — `100%` offline pass) | `src/tests/` |

---

## 2. Cấu trúc Mục lục (`_toc.yml`) & Quy định Chung của Book

### 2.1. Yêu cầu từ phản hồi QA của thầy
- **[QA #6 & #16.2]:** Link Jupyter Book phục vụ chung cho Milestone 1 và Milestone 2, yêu cầu tổ chức thành **1 quyển Book gồm 2 chương/phần tương ứng với 2 milestones này**; hạn chỉnh sửa nội dung cuối cùng là **deadline nộp Poster** (không đổi URL GitHub Pages).
- **[QA #14]:** Ngôn ngữ của Book theo chương trình học (**Chương trình Chuẩn: Tiếng Việt**; TCTA: Tiếng Anh). Bản hiện tại viết tiếng Việt xuyên suốt — giữ nguyên nhất quán.
- **[QA #21]:** Bắt buộc có **tên các thành viên trong nhóm** trên GitHub Pages (có thể không ghi MSSV để tránh lộ thông tin cá nhân).
- **[QA #24]:** Link nộp mục Repository là `https://github.com/GiaaPhuc/arxiv-pipeline`, link nộp mục Jupyter Book là `https://giaaphuc.github.io/arxiv-book/`.

### 2.2. Vấn đề nhỏ ở `_toc.yml` hiện tại và cách chỉnh đẹp nhất
Hiện tại trên `giaaphuc.github.io/arxiv-book/`, file `m1/01_overview` đang được khai báo làm `root:` nên bị đẩy ra **bên ngoài** nhóm `Milestone 1 — Thu thập Dữ liệu` trên thanh điều hướng trái (khiến mục Milestone 1 nhảy thẳng vào `Chương 2`).

**Khuyến nghị chỉnh `intro.md` + `_toc.yml` bên repo `arxiv-book`:**
1. Tạo 1 file trang bìa ngắn `intro.md` làm `root: intro` (chứa tên đề tài, tóm tắt liên hoàn 3 cột mốc, link repo GitHub, và **Bảng 5 thành viên nhóm + phân công vai trò A–E** không kèm MSSV theo **[QA #21]**).
2. Tổ chức `_toc.yml` rõ ràng theo đúng 2 phần bắt buộc của Book (**Milestone 1** và **Milestone 2**), còn **Milestone 3** giữ lại làm phần tổng kết/mở rộng:

```yaml
format: jb-book
root: intro
parts:
  - caption: "Chương I — Milestone 1: Thu thập Dữ liệu (Data Crawling)"
    numbered: true
    chapters:
      - file: m1/01_overview
      - file: m1/02_implementation
      - file: m1/03_data_statistics
      - file: m1/04_performance
  - caption: "Chương II — Milestone 2: Phân tích Cú pháp & Chuẩn hóa"
    numbered: true
    chapters:
      - file: m2/01_parsing_hierarchy
      - file: m2/02_standardization_dedup
      - file: m2/03_parsing_statistics
      - file: m2/04_parser_performance
  - caption: "Phụ lục Mở rộng — Milestone 3: Đối sánh Tham chiếu & Hugging Face"
    numbered: true
    chapters:
      - file: m3/01_cleaning_features
      - file: m3/02_labelling_ranking_hf
      - file: m3/03_matching_statistics
      - file: m3/04_model_evaluation_hf
```

> **Lưu ý an toàn link:** Nếu nhóm đã nộp link trực tiếp `.../arxiv-book/m1/01_overview.html` ở đâu đó thì việc giữ nguyên đường dẫn `m1/01_overview` giúp link cũ **không bị gãy (vẫn HTTP 200)**, trong khi trang gốc `.../arxiv-book/` có trang bìa chuẩn mực.

---

## 3. Cập nhật Chi tiết Phần Milestone 1 (`m1/01` → `m1/04`)

### 3.1. Chương 1 — `m1/01_overview` (Tổng quan & Phạm vi Thu thập Dữ liệu)

1. **Mục `1.1` (Tổng quan kiến trúc & Chiến lược chạy chung toàn nhóm):**
   - Đổi tên phiên bản từ `arxiv-pipeline v3.14` thành **`arxiv-pipeline v3.18`**.
   - Bổ sung 1 đoạn ngắn nêu rõ chiến lược vận hành tập trung (**Quyết định D8 — khớp [QA #1, #5, #8]**): Nhóm gộp cả 5 dải (`5 × 5.000 = 25.000` arXiv ID, không phân biệt danh mục/chuyên ngành) để thu thập và xử lý chung trên môi trường **Google Colab CPU-only** nhằm tiết kiệm tài nguyên tính toán và tránh nhân bản truy vấn S2/arXiv; đến cuối Milestone 3 mới tách theo từng dải `range_0..range_4` để đóng gói lên Hugging Face cá nhân.
   - Cập nhật tóm tắt thông số kỹ thuật ở đoạn giới thiệu (xóa các con số cũ `3.15s`, `idle_gap >= 3.05s`, `50 MiB recharge`, thay bằng: **Start-to-Start `3.05 s + jitter 0..0.10 s`**, đệm hậu tải `0.25 s`, **HTTP/2 kết nối đơn + Zero-Body Header Early-Reject**, **Cut-Through In-Flight Extraction `O(1)` RAM**, **Traffic-Shaping Interleaver & Leaky Byte-Budget `360 MiB / 180 s`**, và **Semantic Scholar High-Density Batch `150` + Save-First Isolation**).

2. **Mục `1.2` (Phạm vi Định danh & Quy ước Bao phủ Phiên bản + Timestamp):**
   - Giữ bảng 5 dải ID (`2408.11045–16044`, `2408.06045–11044`, `2408.01045–06044`, `2407.07839–12838`, `2406.07938–12937`). Ghi chú thêm rằng cả 5 dải đều đã được kiểm chứng nằm gọn trong phạm vi ID hợp lệ trong tháng của arXiv (tháng 06–08/2024 đều có `> 18.000` bài nộp/tháng, không bị tràn quá ID cuối tháng — **[QA #22.2]**).
   - **Bổ sung hộp thông tin Mốc thời gian thu thập (`Crawl & Snapshot Timestamp` — bắt buộc theo [QA #23.3]):**
     - Ghi rõ **ngày phát hành/tải tệp `arxiv-metadata-oai-snapshot.json`** (nếu dùng `--snapshot`) và **khoảng ngày thực thi các phiên crawl `/src/` + S2 trên Google Colab** (lấy từ `min(started_at_utc)` đến `max(finished_at_utc)` trong `performance.json`). Giải thích: vì arXiv liên tục nhận thêm bản sửa đổi (`v2, v3...`) theo thời gian thực, toàn bộ số lượng phiên bản (`num_versions`) trong báo cáo được khóa nhất quán tại mốc thời gian thu thập này.
   - Cập nhật lại các con số tổng kết dẫn nhập (tổng số version, số bài thành công, tổng số tham khảo, dung lượng đĩa cuối) khớp với `summary.json` mới sau khi crawl xong.

3. **Mục `1.3` (Tổ chức Dữ liệu Cục bộ & Hai Bảng Quy định):**
   - **Làm rõ thiết kế lưu trữ trung gian theo [QA #23.4]:** Giải thích vì sao nhóm lưu `paper.json` + `s2_references.raw.json.gz` ở cấp bài báo và nhân bản `references.json` (`s2_scope = "paper"`) kèm `meta.json` (`version_date`) ở từng thư mục `v1..vN` — do Semantic Scholar Graph API chỉ lập chỉ mục danh sách tham khảo ở cấp công bố (`ARXIV:<base_id>`), không tách biệt theo từng bản nháp `vK`, trong khi lịch sử ngày cập nhật của từng bản đã được bảo toàn đầy đủ trong `paper.json["revised_dates"]` và `vK/meta.json["version_date"]`.
   - **Làm rõ trường `ref_submission_date` theo [QA #19, #20]:** Ghi chú rõ trong bảng mô tả lược đồ `references`: trường `ref_submission_date` lấy từ `publicationDate` (hoặc năm `year` `YYYY` khi khuyết ngày-tháng) do Semantic Scholar trả về nhằm đồng bộ tên trường với bảng `papers`.
   - **Làm rõ lý do giữ tệp `.bbl` bên cạnh `.tex` và `.bib` theo [QA #18]:** Ghi chú ở sơ đồ cây `v1..vN/tex/` rằng ngoài `.tex` và `.bib`, bộ trích xuất giữ lại cả tệp `.bbl` nguyên bản (bảo toàn SHA-256 gốc, không chỉnh sửa tại M1) để ở Milestone 2 bộ phân tích `bib_extractor.py` chuyển đổi ngược các mục `\bibitem` trong `.bbl` về bản ghi `bibtex` chuẩn cho những bài báo tác giả không nộp kèm `.bib`.

---

### 3.2. Chương 2 — `m1/02_implementation` (Quá trình Triển khai & Công cụ Sử dụng)

Đây là chương có **nhiều chi tiết kỹ thuật cũ của `v3.14` nhất** cần thay thế sang `v3.18`:

#### A. Mục `2.1` — Quy trình Thu thập Đầu–Cuối
1. **Bước 1 — Khám phá siêu dữ liệu (`snapshot.py`, `discovery.py`):**
   - Giữ mô tả 2 chế độ: Offline Kaggle Snapshot (`--snapshot`, quét streaming JSONLines trong `< 60 s`, `0` request mạng) và Online Atom API (`id_list` lô `250`, xác nhận lại các ID vắng mặt bằng lô nhỏ `50` trước khi gán `not_found`) + OAI-PMH `arXivRaw` (`https://oaipmh.arxiv.org/oai`, `--enable-oai-dates`) bù ngày phiên bản trung gian `v2..v_{N-1}`.
   - Bổ sung ghi chú theo **[QA #1]**: Ba phương thức trong đề (arXiv Atom API, OAI-PMH, Kaggle Dataset) đóng vai trò hộp công cụ bổ trợ lẫn nhau — nhóm kết hợp ưu thế `O(1)` mạng của Kaggle Snapshot cho khám phá hàng loạt với Atom API / OAI-PMH cho kiểm chứng trực tuyến và phục hồi ngày sửa đổi.

2. **Bước 2 — Tải gói nguồn, điều tiết lưu lượng & giải nén tối ưu (`http.py`, `source.py`, `pipeline.py`):**
   Cần **thay thế toàn bộ các thông số cũ của `v3.14`** bằng 5 cơ chế hiện hành của `v3.15–v3.17.1`:
   - **Transport HTTP/2 Kết nối đơn & Keep-Alive bền vững (`v3.15–v3.16`):**
     - Hỗ trợ `--http2` qua `httpx[http2]` với `Limits(max_connections=1, max_keepalive_connections=1)` (tự động fallback về HTTP/1.1 `_SingleConnAdapter(pool_connections=1, pool_maxsize=1)` với `TCP_NODELAY=1` nếu môi trường thiếu `h2`).
     - Nâng chu kỳ xoay vòng phiên kết nối từ `120` lên **`500` request** (`recycle_session()` gom tối đa 1 lần/request), chỉ chủ động làm mới phiên khi gặp gói khổng lồ **`>= 50 MiB`** (thay vì `30 MiB`), khi `TTFB >= 5.0 s` (thay vì `3.5 s`), khi vượt trần ngân sách byte trượt, hoặc khi stream HTTP/2 đóng sớm.
   - **Zero-Body `Content-Type` Header Early-Reject (`v3.16`):**
     - Thay vì phải tải chunk đầu tiên để đọc `512 bytes` magic như `v3.14`, ngay khi nhận HTTP response header có `Content-Type` là `application/pdf` hoặc `application/zip` (kèm `Content-Length`), crawler ngắt luồng ngay lập tức với **`wire_bytes = 0`** (`Status.NO_SOURCE`, trên HTTP/2 gửi khung `RST_STREAM` hủy riêng stream đó mà vẫn giữ ấm kết nối TCP/TLS chung). Kiểm tra `512 bytes` magic đầu tiên vẫn được giữ làm lớp bảo vệ thứ hai khi máy chủ không trả `Content-Type` tường minh.
   - **Nhịp độ Start-to-Start `3.05 s` + Đệm hậu tải `0.25 s` & Deficit Breather (`v3.15` — giải trình trực tiếp [QA #26]):**
     - *Xóa thông số cũ:* Bỏ `Start-to-Start 3.15 s` + `End-to-Start min_idle_gap = 3.05 s` và bỏ hoàn toàn cơ chế ngủ cứng `12.0 s` mỗi `50 MiB`.
     - *Thay bằng thiết kế hiện tại:* Đặt `arxiv_min_interval = 3.05 s + jitter (0..0.10 s)` (trung bình `~3.10 s`, luôn `>= 3.00 s` đúng chuẩn `arxiv.py`) đi kèm `arxiv_min_idle_gap = 0.25 s` và giới hạn luồng tải mượt `--max-stream-mbps = 6.0 MiB/s`.
     - *Lập luận kỹ thuật theo [QA #26]:* Giảng viên khuyến nghị cân nhắc nghỉ sau khi tải xong để hạn chế retry hoặc thiết kế cơ chế linh hoạt cho cả hai trường hợp. Thực nghiệm của nhóm trên 6 phiên Colab cho thấy nếu áp cứng End-to-Start `>= 3.05 s` cho mọi gói nhỏ, thời gian tải mạng bị cộng dồn trực tiếp làm mất thêm `~4,5 giờ / 5.000 ID` dù băng thông rất nhẹ. Do đó nhóm áp dụng cơ chế **thích ứng hai chế độ**:
       1. Với gói nhỏ và vừa (`<= 18 MiB`, tải xong trong `<= 2.80 s`), nhịp Start-to-Start `3.05–3.15 s` tự động đảm bảo khe nghỉ sau tải mà không tốn thời gian chờ thừa.
       2. Với gói lớn tải nhanh hoặc khi CDN chớm nghẽn, hàm `_compute_proportional_breather` tự động bù phần thiếu hụt `max(0, wire_bytes / (6.0 MiB/s) - (TTFB + download_seconds))` (trần `6.0 s`), kết hợp **Defensive Soft-Pacing (`3.80–4.50 s` trong 50 req)** khi băng thông Fastly `HIT` giảm dưới `3.0 MiB/s` — đạt đúng hiệu quả chống 429/retry của End-to-Start trên các gói nặng.
   - **Traffic-Shaping Interleaver & Leaky Byte-Budget Governor (`v3.17–v3.17.1`):**
     - Giải quyết hiện tượng cụm bài nặng liên tiếp làm cạn Token Bucket của Fastly CDN: (1) Hoán vị thứ tự bài trong mỗi chunk `>= 16` ID theo bước nhảy tỷ lệ vàng nguyên tố cùng nhau (`0.618...`) để rải đều các bài nặng cùng ngày; (2) Khi gặp phiên bản `>= 25 MiB`, đẩy các phiên bản còn lại (`v2..vN`) của bài đó vào `heavy_interleave_queue` (tối đa `32`, xen tối thiểu `15` phiên bản nhẹ trước khi rút tiếp, xả round-robin cuối pass); (3) Theo dõi ngân sách byte cửa sổ trượt Leaky Bucket `180 s` (ngưỡng vàng `220 MiB` tạm ngưng rút hàng đợi nặng; ngưỡng đỏ `360 MiB` trên gói `>= 1 MiB` xoay session và nghỉ hồi phục `10–60 s` *sau* khi đã đẩy gói vừa tải vào `extract_queue` để `extract-worker` vẫn giải nén song song trong lúc ngủ).
   - **Giải nén Cut-Through In-Flight `O(1)` RAM (`v3.16`) song hành cùng Pool `extract-worker` (**[QA #17, #22.1]**):**
     - Nhấn mạnh định hướng tối ưu đúng gợi ý của thầy ở **[QA #17 & #22.1]**: giữ nghiêm ngặt **1 kết nối tuần tự duy nhất** tới `export.arxiv.org`, không bao giờ bắn đa luồng song song lên máy chủ arXiv, nhưng dùng kiến trúc đa luồng cục bộ để **xử lý dữ liệu đã lấy về trong lúc chờ mạng**.
     - Khi bật `--in-flight-extract`, `download_and_extract_source()` giải nén luồng `gzip` + `tar` 1-pass trực tiếp từ socket (đệm `128 KiB`, RAM `O(1)`, lọc giữ `.tex`/`.bib`/`.bbl` và băm SHA-256 ngay trên đường truyền, `0 B` ghi tạm vào `.tmp/`); nếu luồng mạng đứt giữa chừng, bài được defer sang cuối pass và tự động chuyển về `download_source()` hỗ trợ nối byte `HTTP 206 Range` từ tệp `.resume-{id}-v{K}.part`.

3. **Bước 3 — Thu thập siêu dữ liệu tham khảo Semantic Scholar (`s2.py`, `pipeline.py` — `v3.18`):**
   - *Cập nhật thông số lô:* Nâng `--s2-batch-size` mặc định từ `25` lên **`150` bài/lô** (`POST /graph/v1/paper/batch`, dung lượng phản hồi trung bình `~3,9 MB` nằm an toàn dưới trần `10 MB` của S2, giảm **`6×`** tổng số HTTP request).
   - *Mở rộng Auto-Bisection & 10k-Ref Guard:* Tự động chia đôi đệ quy (`N/2`) không chỉ với `HTTP 413` / `5xx` mà cả khi gặp `HTTP 400` vượt giới hạn kích thước phản hồi hoặc khi tổng số tham khảo nhúng trong lô chạm ngưỡng `>= 9.500` refs (tự tách các bài cuối lô để gọi lại).
   - *Loại bỏ False-Positive `GET /references` & Cô lập Save-First:* Phân tích thực nghiệm cho thấy S2 thường trả `referenceCount > len(references)` chỉ vì một vài trích dẫn không liên kết được (`citedPaper = null`); bản cũ gọi bù `GET /references` cho mọi trường hợp lệch nhỏ này dẫn tới hàng nghìn request lẻ và bão `HTTP 429`. Bản `v3.18` siết điều kiện `_references_truncated()` (chỉ phân trang khi `len == 0`, `len >= 500`, hoặc thiếu nghiêm trọng `< 50%` với `referenceCount >= 10`), đồng thời áp dụng **Save-First Isolation** (`on_result` lưu ngay xuống đĩa toàn bộ các bài đã đủ dữ liệu trong lô trước khi vét riêng bài bị cắt).
   - *Fast S2 Backoff & AIMD:* Tách thang chờ `429` riêng cho S2 (`2 → 4 → 8 → 15 s + jitter`, tôn trọng `Retry-After` `1..120 s`), điều tiết `AdaptiveRateLimiter` theo AIMD (`+0,4 s` khi gặp `429` tới trần `3,0 s` có API key, giảm `-0,25 s` sau mỗi `5` lần `200 OK` liên tiếp), `.strip()` khóa `S2_API_KEY`, và hỗ trợ tối đa `2` vòng vét deferred (`30 s` cooldown) kèm checkpoint định kỳ trong chế độ `--s2-only`.
   - *Về xử lý `HTTP 404` trên S2 (**[QA #23.1]**):* Trình bày rõ khi S2 trả `404` (hoặc `null` trong batch), hệ thống ghi nhận trạng thái `not_indexed` để thống kê trung thực độ phủ của S2 (còn công cụ bóc tách `.bib/.bbl` cục bộ chỉ là tiện ích dự phòng tùy chọn và được tách riêng nguồn gốc dữ liệu nếu sử dụng).

#### B. Mục `2.2` & `2.3` — Bảng Module, Chịu lỗi CDN & Kiểm thử
- Bổ sung module `src/crawler/preflight.py` (kiểm tra nhanh 4 ID lạnh bằng `Range` giới hạn byte để chấm hạng định tuyến POP/VM `Grade A/B/C` trên Colab và chạy đối chứng A/B HTTP/1.1 vs HTTP/2).
- Bổ sung cơ chế **CDN-Aware Self-Healing**: đọc header `X-Cache` / `X-Served-By` tách riêng `HIT` thuần của Fastly khỏi `SHIELD_HIT` và `MISS`, phân loại `429` (`TTFB >= 9,0 s` nghẽn hàng đợi origin vs edge fast), và tự động ngủ hồi phục `300 s` (tối đa `3` lần/phiên) nếu `HIT` thuần suy giảm `< 2,0 MiB/s` qua 2 cửa sổ 100-request liên tiếp.
- Cập nhật tổng số kiểm thử tự động từ `765` lên **`816` unit tests** (`319` test M1 + `334` test M2 + `163` test M3, `100%` chạy offline qua `responses` và synthetic fixtures).

---

### 3.3. Chương 3 — `m1/03_data_statistics` (Thống kê & Phân tích Dữ liệu Thu thập)

Các bảng và biểu đồ của Chương 3 được sinh tự động từ `stats/{summary.json, papers_stats.csv, versions_stats.csv}`. Sau khi chép bộ `stats/` mới vào repo sách, cần cập nhật phần bình luận phân tích ở 4 điểm sau:

1. **Mục `3.1` & `3.2` — Giải thích tường minh các trạng thái lỗi và biên (`not_found` trên `/src/`, `no_source`, `corrupt`) theo [QA #11, #23.2]:**
   - **Các version trả `HTTP 404` trên `/src/<id>v<n>` (`status = "not_found"` — [QA #23.2]):** Giải thích rõ dù siêu dữ liệu bài báo có liệt kê phiên bản `v<n>`, máy chủ nguồn `export.arxiv.org/src/<id>v<n>` vẫn trả `HTTP 404` trên một số ít bản nháp (thường do lỗi biên dịch phía arXiv hoặc bản nháp bị rút/ẩn nguồn). Theo đúng hướng dẫn của giảng viên, pipeline **vẫn khởi tạo thư mục phiên bản và ghi bản ghi rỗng `v<n>/meta.json` (`kept_bytes = 0`, lưu kèm `error = "HTTP 404 on /src/"`) giống như phiên bản chỉ có PDF (`no_source`) để không bị đứt quãng dãy `v1..vN`, nhưng đánh dấu trạng thái `not_found` và KHÔNG tính vào số bài/phiên bản thu thập thành công (`papers_success`, `success_rate_pairs`)**.
   - **Các gói `corrupt` (nếu có, ví dụ gói chứa tên tệp trùng thiết bị hệ thống `aux`/`con` hoặc nén hỏng — [QA #11]):** Phân tích cụ thể cơ chế kiểm tra an toàn đường dẫn (`_safe_member_path`) và kiểm tra toàn vẹn CRC32/ISIZE của `_FastGzipStream` giúp cô lập gói lỗi mà không làm dừng toàn bộ tiến trình 25.000 bài.
   - Cập nhật lại các con số trong văn bản thuyết minh (số lượng version `ok`, `no_source`, `not_found`, trung bình version/bài...) cho khớp hoàn toàn với bảng tự sinh mới.

2. **Mục `3.3` — Hiệu quả loại bỏ hình ảnh & Giữ lại `.tex / .bib / .bbl` ([QA #18]):**
   - Cập nhật các con số trung bình byte trước và sau khi lọc từ `summary.json` mới, đồng thời ghi chú rõ phần dung lượng giữ lại (`kept_bytes`) bao gồm `.tex`, `.bib` và cả `.bbl` (chiếm tỷ lệ rất nhỏ nhưng bảo toàn nguồn tham khảo cho các bài biên dịch sẵn không kèm `.bib`).

3. **Mục `3.4` — Minh bạch hóa tỷ lệ `HTTP 404` trên Semantic Scholar ([QA #23.1]):**
   - Kiểm tra kỹ con số `s2_states["not_indexed"]` và `s2_coverage_papers` trong `summary.json`:
     - Nếu chạy chính thức **không bật `--enable-bib-fallback`**: báo cáo trực tiếp số bài `not_indexed` (S2 trả `HTTP 404`) và giải thích nguyên nhân do độ trễ lập chỉ mục của S2 đối với một số bài nộp/chuyên ngành hẹp, đúng như **[QA #23.1]**.
     - Nếu trong dữ liệu có các bài dùng `local_bib`: **bắt buộc tách riêng** số bài S2 trả `200 OK` thật và số bài S2 trả `404` (được bù từ `.bib/.bbl` cục bộ) để tỷ lệ thành công siêu dữ liệu tham khảo của S2 phản ánh trung thực hành vi của API.

---

### 3.4. Chương 4 — `m1/04_performance` (Đánh giá Hiệu năng Bộ Thu thập)

Đây là chương quan trọng nhất phục vụ chấm tiêu chí **"Hiệu năng của bộ thu thập"** và các quy định trong **[QA #9, #12, #13, #15, #16.1, #17]**:

1. **Mục `4.1` — Môi trường Kiểm thử Google Colab CPU-only ([QA #9, #12, #16.1]):**
   - Ghi rõ cấu hình phần cứng của các phiên **Google Colab CPU-only** đã chạy trọn bộ **25.000 ID** của nhóm (số vCPU, dung lượng RAM hệ thống, phiên bản Python lấy tự động từ `performance.json["environment"]`).

2. **Mục `4.2` — Phân tích Thời gian Chạy Hệ thống ([QA #13, #15, #17]):**
   - **Quy tắc tính Tổng Runtime bắt buộc theo [QA #13]:**
     - Nêu rõ: **Tổng thời gian thực thi đầu–cuối (End-to-End Wall-Clock Time) bao gồm toàn bộ thời gian tải mạng, giải nén, chờ giới hạn tốc độ (`RateLimiter`), và mọi khoảng chờ retry / backoff / cooldown khi gặp lỗi mạng hoặc HTTP `429`/`5xx`** (chỉ loại trừ `idle_gap_seconds` là khoảng thời gian tắt máy nghỉ giữa hai phiên Colab không liên tiếp).
     - **Sửa công thức ghép chặng nếu chạy 2 giai đoạn tách rời:** Ở bản cũ viết `max(T_arXiv, T_S2)` (chỉ đúng khi `s2-worker` chạy song song trong cùng phiên với luồng tải arXiv). Khi nhóm chạy **Giai đoạn 1 (`--skip-s2`)** trên các phiên Colab đầu rồi chạy **Giai đoạn 2 (`--s2-only`)** ở phiên tiếp theo, tổng thời gian thực thi hệ thống phải là **tổng thời gian thực của toàn bộ các phiên Giai đoạn 1 + Giai đoạn 2** (bao gồm cả thời gian quét `--snapshot` đầu vào)!
   - **Bổ sung Bảng Đối chiếu Nhật ký Từng Phiên Chạy Colab (*bắt buộc theo [QA #15]*):**
     - Thêm một cell code Python đọc mảng `sessions` từ `stats/performance.json` (hoặc `summary.json["benchmark_summary"]["sessions"]`) để sinh tự động **Bảng Nhật ký Phiên Colab** gồm các cột:
       `Phiên (#)` | `Bắt đầu (UTC)` | `Kết thúc (UTC)` | `Chế độ chạy` | `Số bài hoàn tất` | `Active Stage (s)` | `Retry & Cooldown (s)` | `Wall Time Phiên (s)` | `Đỉnh RAM (MiB)`
     - Nhờ bảng này, giảng viên có thể đối chiếu trực tiếp từng phiên checkpoint Colab với con số tổng hợp cuối cùng đúng như yêu cầu tại **[QA #15]**.
   - **Phân tích so sánh hiệu quả tối ưu `v3.14` → `v3.18` (Điểm sáng cho báo cáo & Poster — [QA #13, #17]):**
     - So sánh trực tiếp với mốc tham khảo trung vị năm trước mà thầy nêu ở **[QA #17]** (`7–8 giờ / 5.000 ID`, tức `~35–40 giờ / 25.000 ID`) và với phiên bản trước của nhóm (`67,30 giờ`, bị phạt `23,26 giờ` chờ S2 429 và `10,19 giờ` chờ arXiv 429):
       - Phân tích đóng góp của **Start-to-Start `3.05s` + gỡ phạt kép End-to-Start**;
       - Phân tích tỷ lệ **Fastly CDN `HIT` / `SHIELD_HIT` / `MISS`** và số byte tiết kiệm nhờ **Zero-Body Header Early-Reject** (từ `performance.json["arxiv"]`);
       - Phân tích mức giảm số lượng HTTP `429` và thời gian chờ của Semantic Scholar nhờ **High-Density Batch `150` + Save-First + Diệt False-Positive `/references`**.

3. **Mục `4.3` — Mức Sử dụng Bộ nhớ RAM & Lưu trữ Đĩa ([QA #9]):**
   - Cập nhật đỉnh RAM (`ram_peak_bytes` / `max_session_peak_rss_mib`), RAM trung bình theo trọng số thời gian (`session_time_weighted_avg_rss_mib`), và dung lượng đĩa đỉnh / cuối (`disk_peak_bytes`, `disk_final_bytes`) đo trên Google Colab.
   - Giải thích vì sao đỉnh RAM giữ ở mức cực thấp và phẳng (`O(1)` theo quy mô kho ngữ liệu) nhờ giải nén trực tiếp trên luồng (`--in-flight-extract` `128 KiB` / `_FastGzipStream` `512 KiB`) và hàng đợi có trần bộ nhớ.

---

## 4. Cập nhật & Rà soát Phần Milestone 2 (`m2/01` → `m2/04`) & Milestone 3 (`m3/01` → `m3/04`)

Khi kho ngữ liệu Milestone 1 mới hoàn tất, cần rà soát đồng bộ các chương tiếp theo trên Book như sau:

1. **Chương 5 (`m2/01_parsing_hierarchy`) & Chương 6 (`m2/02_standardization_dedup`):**
   - **Khẳng định tuân thủ môi trường CPU-only không gọi API ngoài ([QA #22.3]):** Thêm 1 ghi chú ngắn ở mục `5.1` khẳng định toàn bộ quy trình phân tích cú pháp, chuẩn hóa toán học, tách câu và trích xuất tham khảo đều là thuật toán tất định chạy cục bộ 100% trên CPU (không dùng mô hình ngoại vi hay gọi API bên ngoài).
   - **Làm nổi bật luồng khôi phục `.bbl` ([QA #18]):** Ở mục `6.4` (Luồng 2), liên hệ trực tiếp với quyết định giữ tệp `.bbl` từ Milestone 1: vì `.bbl` là sản phẩm biên dịch từ `.bib`, bộ phân tích `parse_bibitems()` đóng vai trò chuyển đổi ngược các mục `\bibitem` (hỗ trợ `\bibinfo`, `\bibfield`, `\showarticletitle` và chuỗi 4 mẫu câu) về đúng lược đồ `BibtexRow` chuẩn mà không làm mất nguồn tham khảo.
   - **Bổ sung phân tích tình huống đặc thù References viết tay không dùng `.bib` và không dùng `\bibitem` ([QA #25]):**
     - Tại mục `6.4` (hoặc mục thảo luận hạn chế/ứng biến thực tế), bổ sung 1 tiểu mục ngắn giải trình rõ trường hợp tác giả tự gõ danh mục tham khảo dưới `\section{References}` bằng văn bản thường hoặc `\begin{enumerate}\item...\end{enumerate}`:
       - *Ở tầng cấu trúc (`hierarchy.py`):* Bộ lọc tiêu đề mục đã nhận diện và cắt triệt để khối này ra khỏi cây văn bản (đảm bảo `0%` rò rỉ mục tham khảo vào bảng `elements`).
       - *Ở tầng trích xuất (`bib_extractor.py`):* Ghi rõ kết quả xử lý của nhóm (nếu nhóm giữ nguyên chỉ trích xuất `.bib`/`\bibitem` để đảm bảo khóa trích dẫn `\cite{key}` có thật trong văn bản phục vụ Milestone 3 thì nêu rõ số lượng bài rơi vào trường hợp này trên 25.000 bài và lý do tránh sinh khóa giả; nếu nhóm bổ sung thêm nhánh tách `\item`/đoạn đánh số thì báo cáo số mục thu hồi thêm được).

2. **Chương 7 (`m2/03_parsing_statistics`) & Chương 8 (`m2/04_parser_performance`):**
   - **Đồng bộ với kho M1 mới:** Nếu đợt crawl M1 mới làm thay đổi số lượng version `ok` so với con số `38.737` cũ, cần chạy lại `run_parse.py` + `parsing/validate_m2.py` để chép bộ `m2_summary.json`, `m2_papers_stats.csv`, `m2_elements_kind.csv` mới sang `arxiv-book/stats/m2/`, giúp tổng số bài/version ở Chương 7 khớp khít 100% với Chương 3.
   - **Kiểm tra môi trường Benchmark Colab CPU ở Chương 8 ([QA #9, #16.1]):** Bản Chương 8 hiện tại ghi `12 workers`, RAM đỉnh `3,934 GiB`. Cần ghi chú rõ cấu hình instance Google Colab CPU đã dùng cho lần đo đó (hoặc chạy lại `colab_parse.ipynb` trên Google Colab CPU chuẩn — lưu ý chạy riêng phiên Full Run sạch để không cộng dồn thời gian của ô Pilot 1-worker vào `m2_performance.json`).

3. **Chương 9–12 (`m3/01` → `m3/04` — Phần mở rộng Milestone 3):**
   - Các chương M3 hiện đã hoàn chỉnh trên `range_0` (`2408.11045–2408.16044`) và khớp với bộ dữ liệu đã đóng gói Hugging Face. Chỉ cần thêm 1 dòng ghi chú nhỏ ở đầu Chương 11 rằng kết quả thực nghiệm M3 trên Book minh họa trên phân vùng `range_0` (5.000 ID) đã đóng băng cho đợt nộp Hugging Face cá nhân (trong khi tổng hợp liên dải trên cả 5 thành viên được trình bày trên Poster A2).

---

## 5. Checklist Thực thi Sau Khi Crawl Xong (Trước khi Build & Deploy Pages)

Ngay khi tiến trình crawl hiện tại hoàn tất 25.000 ID trên Colab:

- [ ] **Bước 1 — Chạy nghiệm thu & kiểm toán kho dữ liệu M1 trên Colab:**
  ```bash
  python src/crawler/validate_corpus.py --data-dir /content/data
  python src/crawler/audit_corpus.py --data-dir /content/data --output /content/data/stats/audit_report.json
  python src/crawler/export_data.py --data-dir /content/data --out-dir /content/data/stats
  ```
- [ ] **Bước 2 — Kiểm tra 3 con số nhạy cảm trong `data/stats/summary.json` & `performance.json`:**
  1. Kiểm tra `s2_states` xem số bài S2 trả `HTTP 404` (`not_indexed`) đã được tách riêng khỏi các bài dùng `local_bib` chưa (**[QA #23.1]**).
  2. Kiểm tra `pairs_by_status["not_found"]` (số version trả `404` trên `/src/`) và `ids_not_found` (**[QA #23.2]**).
  3. Kiểm tra mảng `sessions` trong `performance.json`: đảm bảo đủ mọi phiên Colab đã chạy (cả Giai đoạn 1 `--skip-s2` và Giai đoạn 2 `--s2-only`), mọi phiên đều có `started_at_utc`, `finished_at_utc`, `colab_cpu = true` (**[QA #9, #13, #15]**).
- [x] **Bước 3 — Đồng bộ mã nguồn `arxiv-pipeline` trước khi merge vào `main`:**
  - Trong `src/notebooks/colab_crawl.ipynb` (ô code 1), đổi `BRANCH = "m1/a-crawler-optimization"` về `BRANCH = "main"`.
  - Chạy `ruff check . && ruff format --check . && pytest` đảm bảo xanh toàn bộ `816` tests.
- [ ] **Bước 4 — Cập nhật dữ liệu & nội dung bên repo `arxiv-book`:**
  - Chép các tệp thống kê mới (`summary.json`, `performance.json`, `papers_stats.csv`, `versions_stats.csv`; và `m2_*` nếu có chạy lại M2) vào thư mục `stats/` của `arxiv-book`.
  - Cập nhật `_toc.yml` + `intro.md` (Mục 2) và văn bản thuyết minh của `m1/01..04` + `m2/01..04` theo danh mục ở Mục 3 và Mục 4 (đặc biệt thêm cell sinh **Bảng Nhật ký Từng Phiên Colab** ở `m1/04_performance`).
  - Biên dịch lại sách (`jupyter-book build .`), kiểm tra mọi bảng và biểu đồ đều được sinh động lúc build (không dùng ảnh chụp màn hình tĩnh) và không lệch số giữa lời văn với bảng tự sinh, trước hạn cuối cùng là **deadline nộp Poster** (**[QA #6]**).
