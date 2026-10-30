# STATUS — Milestone 1 (arXiv crawler)

> File sống, **nguồn sự thật duy nhất** về trạng thái dự án **so với đề** trong `project requirements/`.
> Agent đọc **mục 0** là đủ để nắm đề và chỗ nhóm đang đứng. Chỉ mở file đề khi cần câu chữ gốc, ví dụ mã trong đề, hoặc chi tiết chấm chưa nằm ở bảng dưới.
> Cập nhật mỗi khi xong việc / đổi quyết định / phát sinh rủi ro.
> Cập nhật lần cuối: **T6 30/10/2026 ~23:45 (Asia/Saigon)** — checkpoint Colab: không backup lại sau mỗi 1000 ID **đã xong** khi resume; copy tăng dần (bỏ qua file size/mtime giống đích). — **189 passed**. Trước: arXiv 429 discovery retry; mount Drive; `CRAWLER_BACKUP_*`. — **Phase 0 ✅ · Phase 1 ✅** (go/no-go). **Phase 2**: crawl **25.000 ID** một máy.
> Ký hiệu: ✅ xong · 🟡 đang làm / một phần · ⬜ chưa bắt đầu · ⛔ bị chặn
> Mã task (T0.1, T1.3...) lấy từ kế hoạch kỹ thuật + playbook của nhóm (chia sẻ riêng, không nằm trong repo). Điều kiện xong của từng module nằm ngay trong docstring đầu file module đó.

---

## 0. Đọc nhanh (agent / người mới chỉ cần đọc phần này)

### Đề và phạm vi

- Môn Nhập môn Khoa học Dữ liệu, dự án nhóm về arXiv. Ba milestone cộng dồn vào **một repo mã**. Hiện **chỉ làm Milestone 1** (thu thập). Milestone 2 (parse LaTeX thành cây, chuẩn hóa, `\bibitem` → BibTeX, bỏ trùng) và Milestone 3 (đối sánh BibTeX với bảng `references`, MRR@5, poster A2 dọc, dataset Hugging Face) **chưa bắt đầu** — không triển khai.
- Nộp Milestone 1 qua Moodle: link repo mã công khai + link Jupyter Book (repo sách riêng, GitHub Pages). **Không nộp dữ liệu** ở mốc này. Viết bằng tiếng Việt. Không nộp trễ.
- Khi nộp, repo mã chỉ được chứa mã (`.py` / `.ipynb`), `requirements.txt` và `README.md`. `STATUS.md`, `AGENTS.md`, `CONTRIBUTING.md`, `plans/`, `project requirements/` đang ở trong repo để làm việc — xử lý lúc đóng băng (R6, T5.3 trong Phase 2).

### Milestone 1 — đề vs hiện trạng

| Hạng mục đề | Đề yêu cầu (rút gọn) | Hiện trạng |
| --- | --- | --- |
| Phạm vi crawl | Dải arXiv ID được giao; **mọi phiên bản** của từng bài. Không thu hoạch cả kho. OAI-PMH và Kaggle chỉ là hộp công cụ | ✅ `src/id_ranges.csv`: 5 dải, **25.000** ID (D8 một máy). Pilot **30 ID** đầu dải 3; crawl đủ là **Phase 2 (T4.1)** |
| Nguồn và lịch sự | Chỉ `export.arxiv.org`, chờ ≥ 3 giây, một kết nối. Semantic Scholar ≥ 1 yêu cầu/giây (mặc định 1,1 s + thích ứng sau 429); khóa API | ✅ Pilot + Colab: arXiv 429 API discovery + `/src/` chờ `Retry-After` (≤4 lần) rồi mới exit 3; S2 batch. Colab: `CRAWLER_ARXIV_MIN_INTERVAL=3.5`, **backup mỗi 1000 ID** (`checkpoint.py`). `--s2-only` chưa code |
| Gói nguồn | Tải từng phiên bản qua `/src/{id}vN`. Chỉ giữ `.tex` và `.bib` bằng cách **trích**, không sửa nội dung. Giữ lệnh hình trong TeX. Bản không có TeX = bản ghi rỗng, không bỏ bài | ✅ Pilot: **57** cặp (paper, version), **51** `ok`, **6** `no_source` (PDF), **0** `failed`. Trích + manifest nguyên tử như thiết kế |
| Siêu dữ liệu | Mỗi phiên bản: tiêu đề, tác giả, ngày nộp, ngày sửa, venue khi có. Mỗi tham khảo S2: tiêu đề, tác giả, ngày, arXiv ID nếu có, S2 ID. Giữ **mọi** tham khảo ở máy | ✅ Pilot: **30/30** bài có S2; **~33,6** ref/bài; **~78 %** ref đủ title/authors/date; **~50 %** ref có arXiv ID. Batch + fallback đã chạy thật; đối chiếu tay 10 ID / 5 bài (Phase 1) |
| Hai bảng (nộp Hugging Face ở Milestone 3) | `papers`: arxiv_id, title, authors, submission_date, revised_dates, venue. `references`: chỉ tham khảo **có arXiv ID** — arxiv_id, ref_arxiv_id, ref_title, ref_authors, ref_submission_date, ref_s2_id | ✅ `export_data` trên corpus pilot → `data/stats/*.parquet` + CSV. Schema đúng `models.py` |
| Kho làm việc | Giữ cục bộ làm đầu vào Milestone 2 và 3. Gợi ý đề: một thư mục mỗi bài | ✅ Pilot qua `validate_corpus`; bố cục `data/papers/`, manifest SQLite, resume đã kiểm trên 30 ID |
| Báo cáo | Jupyter Book: cách thu thập, thư viện, số bài thành công, tỷ lệ thành công, kích thước trung bình trước/sau khi bỏ ảnh, số tham khảo trung bình, tỷ lệ thành công siêu dữ liệu tham khảo. Thời gian wall và RAM trên Colab chỉ CPU. Hình và bảng sinh lúc biên dịch sách | 🟡 [arxiv-book](https://github.com/GiaaPhuc/arxiv-book) + Pages; khung và workflow ✅. **E:** thay số mẫu bằng `data/stats/` pilot (T4.4 / T5.1 Phase 2) |
| Mã nguồn | Sạch, chạy được, có chú thích; trích dẫn nếu dùng tài liệu ngoài | ✅ **186 passed**; `ruff` sạch. End-to-end pilot + review chéo Phase 1 |
| Chấm (5 mục) | Báo cáo, mã, khám phá và tải đủ phiên bản, dữ liệu thu được, hiệu năng so với nhóm khác | 🟡 Có số pilot; chấm đủ khi crawl 25k + sách final (Phase 2) |

### Việc đang làm — **Phase 2** (sau go/no-go 28/10)

- **Go/no-go:** Pilot 30 ID ✅. V̄ pilot **~1,9** phiên bản/bài → ~**47.500** request `/src/`; chỉ chờ 3 s/arXiv ≈ **~40 h** (+ tải/trích). Wall pilot (27–28/10, gồm S2 429 trước batch) cỡ **~2 h** cho 30 ID → nhân tuyến tính **~7 ngày** wall nếu S2 vẫn nghẽn; với batch + key ổn định kỳ vọng **~2–3 ngày** một máy (R1 — cần đo lại sau vài trăm ID đầu T4.1).
- **T4.1 (A):** `python src/run_crawl.py --ranges src/id_ranges.csv` trên máy chính; sao lưu định kỳ `data/` + `manifest.sqlite`.
- **T4.2–T4.4:** retry `failed` theo dải; validate ≥ 20 phiên bản; đẩy `stats/` vào sách.
- **T5.x:** nháp sách trong lúc crawl; đóng băng sáng **4/11**; nộp chiều **4/11** (Book Moodle **6/11** 23:59).
- **Tùy chọn sau M1:** `--s2-only`/staging (plan S4) nếu tách S2 khỏi arXiv — chưa code.

---

## 1. Quyết định đã chốt

| Ngày | Quyết định |
| --- | --- |
| 17/10 | Nguồn dữ liệu: arXiv API (`id_list`) + `/src/{id}vN` trên `export.arxiv.org` + S2 Graph API. Pipeline resumable bằng SQLite manifest; hai worker theo host (≥ 3 s arXiv, ≥ 1 s S2); chỉ giữ `.tex`/`.bib`, giữ cấu trúc thư mục; lưu JSON thô trước |
| 17/10 | Dải ID = 4 trường `start_month,start_id,end_month,end_id`, tính cả hai đầu. ID không tồn tại ghi `not_found`, tách khỏi `failed` |
| 17/10 | Công cụ: Python 3.11, `ruff==0.17.1`, `pytest`, `pre-commit`. Nhánh `m1/<vai>-<mô-tả>`; một PR một reviewer không phải owner; không đẩy thẳng `main` |
| 18/10 | Dời kickoff sang T4 21/10. Mục tiêu nộp chiều 4/11, đóng băng sáng 4/11; crawl đủ dự kiến tối 25/10 sau go/no-go. Hạn 5/11 ghi ban đầu được đính chính thành 6/11 theo ảnh Moodle 21/10 |
| 18/10 | **T0.1.** Khóa `models.py` theo đề. Không đổi trường (`SCHEMA_VERSION` vẫn 1). Họp 21/10 thông báo, không biểu quyết lại. Đổi sau này bằng PR riêng, cả 5 người thấy |
| 18/10 | **T0.2.** Theo đề và API manual mà đề trích dẫn, chưa gọi API. Lô `id_list` = 50 ID gốc. Hậu tố bản mới nhất = số phiên bản. `published` = ngày v1. `updated` của lần gọi gốc = ngày bản mới nhất; ngày bản trung gian lấy bằng `idvK` khi có từ 2 phiên bản. ID vắng mặt sau một lần gọi riêng = `not_found`. Lỗi mạng hết lần thử = `failed`. ID cũ giữ nguyên văn |
| 18/10 | **T0.3.** Đã gọi `export.arxiv.org/src/`, cách nhau ≥ 3,5 giây, một kết nối, không lưu gói. Xem mã HTTP trước magic bytes. tar.gz `1f 8b` + `ustar` thì trích `.tex`/`.bib`. PDF = `no_source`. 404 = `not_found`. Gzip đơn và zip chưa gặp; fixture vẫn dựng giả |
| 18/10 | **T0.4.** Một lần S2 mỗi bài trên ID gốc, `s2_scope: "paper"`. Phân trang bằng `/references?offset=&limit=`. Ngày: `publicationDate`, không có thì `YYYY`, không bịa ngày-tháng. Mỗi Colab một API key. Chưa có key |
| 18/10 | **T0.5.** Chưa hỏi giảng viên. Giữ phương án tạm, không chặn code: chờ 3 giây từ lúc yêu cầu trước kết thúc; bản ghi rỗng = thư mục không TeX + `meta.json`; tỷ lệ tham khảo báo cả (a) % bài có S2, (b) % tham khảo đủ tiêu đề/tác giả/năm, (c) % tham khảo có arXiv ID; sách dùng Jupyter Book v1 |
| 18/10 | **T0.7.** File thực tế có **5 dải**, không phải 20. Mỗi dải 5.000 ID, tổng **25.000**. Mỗi dải nằm trong một tháng, không chồng: tháng 8 `01045–06044`, `06045–11044`, `11045–16044`; tháng 7 `07839–12838`; tháng 6 `07938–12937`. Mỗi người một dải (trách nhiệm), crawl gom một máy (D8, 22/10) |
| 19/10 | Giao việc còn hai phase sau Phase 0. Phase 1: code + pilot, hạn trưa 25/10. Phase 2: crawl + sách + nộp, đóng băng sáng 4/11. Mã T1–T5 giữ nguyên. Ngày 22, 23, 24 và 1/11 là mốc trong phase |
| 19/10 | Họp 21/10 nói lại Phase 0 (leader đã làm), không giao spike. Phiếu Phase 1: B/C/D/E không đợi `http.py`; A đợi ba PR rồi mới ghép pipeline; pilot đợi pipeline. Bỏ phiếu `Leader/phase0-phan-cong-vi.md` |
| 19/10 | Vai A là leader. Họp 21/10 gán B–E, năm dải, và người dự phòng Moodle (một người trong B–E, ưu tiên E) |
| 21/10 | **T1.1 / pilot.** `HostClient` **không** retry 403/429; trả caller để pipeline dừng và báo nhóm (`CONTRIBUTING.md`). Retry client chỉ cho 5xx tạm thời. Phản hồi reviewer C |
| 19/10 | Ba người viết crawler. A: `http`, `manifest`, `pipeline`, `metrics`. B: `ids`, `discovery`, `s2`. C: fixture, `source`. D: `export`, `corpus`, notebook số liệu, gửi link trang xin key. E: sách và `validate_corpus`. D và E vẫn mỗi người một dải và một commit. Test giả `s2` hạn sáng 24/10. Trễ thì A pair với B trong ngày 24/10 |
| 22/10 | **D8 — crawl chung (theo khuyến nghị GV):** gom **25.000** ID trong `id_ranges.csv`, **một máy** chạy `run_crawl.py` (resume). Corpus dùng chung M1–M3; **cuối M3** trích subset theo dải từng người lên Hugging Face. Không mặc định 5 Colab × 5.000 ID |
| 21/10 | Theo ảnh Moodle: Book 6/11, dataset cá nhân 20/11, poster 27/11/2026, đều 23:59. Chưa xác minh trực tiếp Moodle; mục tiêu nội bộ nộp 4/11 giữ nguyên |

**Spike `/src/` ngày 18/10** (User-Agent có link repo, không commit gói):

| URL | HTTP | Content-Type | Kích thước | Nhận dạng | Cách xử lý |
| --- | --- | --- | --- | --- | --- |
| `/src/2408.11045v1` | 200 | `application/gzip` | 10.199.199 byte | `1f 8b`, có `ustar` | tar.gz. Chưa liệt kê thành viên vì gói lớn |
| `/src/hep-th/9901001v1` | 200 | `application/gzip` | 15.221 byte | tar.gz, 1 `.tex` + 4 `.eps` | Trích `.tex`, bỏ `.eps`, giữ cây thư mục |
| `/src/2408.01045v1` | 200 | `application/pdf` | 804.208 byte | `%PDF` | `no_source`, vẫn có bản ghi. ID này là đầu dải 3 |
| `/src/2408.00000v1` | 404 | `text/html` | 126 byte | HTML, không phải gói | `not_found`. Không nhìn magic của trang lỗi |
| gzip một file TeX | — | — | — | chưa gặp | Fixture ngày 22 vẫn dựng giả |
| zip / docx | — | — | — | chưa gặp | Fixture dựng giả, xử lý `no_source` |
| 403 / 429 | — | — | — | chưa gặp | Dừng, báo nhóm, không tăng tốc |

`2408.1045` và `2408.01045` cùng trả một PDF 804.208 byte. Sinh ID vẫn dùng dạng đệm 5 số (`2408.01045`).

---

## 2. Tiến độ theo task

### Phase 0 — chốt 18/10, họp 21/10 chỉ thông báo · **✅ xong 28/10**

| Mã | Việc | Owner | TT | Ghi chú |
| --- | --- | --- | --- | --- |
| T0.1 | Hợp đồng dữ liệu | Leader | ✅ | `models.py` khóa 18/10. Không đổi schema |
| T0.2 | Spike arXiv API | Leader | ✅ | Quyết định theo đề, chưa gọi API. Lô 50. Kiểm tra 10 ID thật là việc T2.1 |
| T0.3 | Spike `/src/` | Leader | ✅ | Bảng ở mục 1. PDF nằm trong dải được giao |
| T0.4 | Spike S2 + xin API key | Leader / B | ✅ | Quyết định spike chốt 18/10; key S2 dùng được trên máy pilot (28/10) |
| T0.5 | Câu còn mở với giảng viên | Leader | ✅ | Chưa hỏi. Phương án tạm thành quy ước, không chặn code |
| T0.6 | Repo, lint, quy ước PR | Leader | ✅ | CI xanh; quy ước `CONTRIBUTING.md` + pre-commit. Repo sách T1.4 (Phase 1) |
| T0.7 | File dải + tổng số ID | Leader | ✅ | `src/id_ranges.csv`: 5×5.000 = 25.000. Không bịa thêm 15 dải |

### Phase 1 — giao họp 21/10, hạn trưa CN 25/10 · **✅ xong 28/10** (pilot 30 ID)

Một phiếu: nền tảng, module, tích hợp, pilot. Phiếu: `Leader/phase1-phan-cong-vi.md`. Tên A–E điền trong họp 21/10. Cột mốc là thứ tự, không phải lần giao mới.

B/C đã đưa code vào main qua PR #6/#7. A tích hợp T3.1; B hoàn thiện metadata và kiểm chứng thật. D/E làm T3.2 trên thư mục mẫu. Pilot T3.3 đợi pipeline/resume; năm bài S2 thật cần key của B.

| Mã | Việc | Owner | Mốc | TT |
| --- | --- | --- | --- | --- |
| T1.1 | `http.py` (RateLimiter; 403/429 stop; retry 5xx) | A | hết 22/10 | ✅ |
| T1.2 | `manifest.py` + ghi nguyên tử (`config.py` đã xong) | A | hết 22/10 | ✅ |
| T1.3 | `ids.py` | B | hết 22/10 | ✅ PR #7; test ID mới/cũ, version, 5.000 ID hai đầu và dải qua tháng đạt |
| T1.4 | Khung Jupyter Book v1 + Pages | E | hết 22/10 | ✅ [arxiv-book](https://github.com/GiaaPhuc/arxiv-book) + [Pages](https://giaaphuc.github.io/arxiv-book/); số pilot cập nhật ở Phase 2 |
| T1.5 | Fixture tổng hợp (`tests/conftest.py`) | C (Quỳnh) | hết 22/10 | ✅ Tám nhóm bắt buộc + sáu mẫu phụ; 14 kiểm tra pass trong worktree sạch |
| T2.1 | `discovery.py` (test giả; 10 ID thật ghi trên PR, không đợi `http.py`) | B | hết 23/10 | ✅ Pilot 30 ID: N phiên bản và ngày khớp arXiv (mẫu 10 ID đã đối chiếu) |
| T2.2 | `source.py` (fixture + client giả) | C (Quỳnh) | hết 23/10 | ✅ PR #6 đã merge; fixture/source đạt trong suite 83 passed/4 skipped; chưa kiểm chứng pipeline thật |
| T2.3 | `s2.py` test giả sáng 24/10; 5 bài thật đợi key của B, trước trưa 25/10 | B | sáng 24/10 | ✅ Batch + fallback trên S2 thật; 5+ bài đối chiếu refs/venue (pilot) |
| T2.4 | Đo mỏng trong pipeline (`metrics.py`) | A | hết 24/10 | ✅ `data/logs/metrics.json` + `performance.json` từ pilot (RSS ~39 MB peak, stage timers) |
| T2.5 | Notebook thống kê trên số giả / pilot. E nhúng vào sách | D + E | trưa 25/10 | ✅ `export_stats` → `data/stats/summary.json` + CSV/parquet pilot; E đồng bộ sách ở T4.4 |
| T3.1 | `pipeline.py` (đợi PR discovery, source, s2 xanh) | A | hết 24/10 | ✅ E2E **30 ID**, resume + S2 worker; 0 `failed` |
| T3.2 | `export.py` + `corpus.py` (D) và `validate_corpus.py` (E), mỗi người một thư mục mẫu | D, E | hết 24/10 | ✅ Validate + export trên corpus pilot |
| T3.3 | Pilot 30–50 ID trên Colab (đợi pipeline resume) | A + cả nhóm | trưa 25/10 | ✅ **30 ID** local (27–28/10); ước lượng giờ/đĩa cho 25k ghi mục 0 |
| T3.4 | Review chéo, lint sạch | Cả nhóm | trưa 25/10 | ✅ **180 passed**, `ruff` sạch (28/10) |

### Phase 2 — giao sau go/no-go trưa 25/10, đóng băng sáng 4/11

| Mã | Việc | Mốc | TT |
| --- | --- | --- | --- |
| T4.1 | Crawl đủ **25.000 ID**, một máy (A vận hành); sao lưu corpus chung | 29/10 | ⬜ |
| T4.2 | Chạy lại `failed` | 1/11 | ⬜ |
| T4.3 | Validate + đối chiếu ≥ 20 phiên bản | 1/11 | ⬜ |
| T4.4 | `stats/` vào repo sách | 1/11 | ⬜ |
| T5.1 | Sách (viết nháp trong lúc crawl) | 3/11 | ⬜ |
| T5.2 | README | 3/11 | ⬜ (có bản khởi tạo) |
| T5.3 | Đóng băng checklist | sáng 4/11 | ⬜ |
| T5.4 | Nộp Moodle. Hạn Book theo ảnh: 6/11/2026 23:59 | chiều 4/11 | ⬜ |

---

## 3. Rủi ro và vấn đề đang mở

| # | Mô tả | Mức | Hành động | Owner |
| --- | --- | --- | --- | --- |
| R1 | 25.000 ID trên **một máy** (D8). Chưa biết V̄. Nếu V̄ ≈ 2, chờ arXiv cỡ **42 giờ** + tải/trích. Colab free có thể ngắt — cân nhắc máy local/VM; manifest resume + sao lưu Drive | TB | Pilot sáng 25/10 ước lượng giờ/đĩa; chọn máy chính trước tối 25/10; bật crawl một luồng `--ranges src/id_ranges.csv` | A |
| R2 | Pilot 30 ID đã chạy; rủi ro còn lại là **quy mô 25k** (định dạng lạ, S2 lệch batch). Hạn nộp 4/11 | TB | Theo dõi `failed` + `metrics.json` trong T4.1; validate mẫu 1/11 | A, B |
| R3 | Key S2 hoạt động trên máy pilot; vẫn có **429** (pilot: 3×, 180 s chờ). Worker tắt sau 403 hoặc ~30 phút 429 liên tiếp | TB | Giữ batch + limiter; máy crawl full dùng cùng key đã pilot | B |
| R4 | CI + pilot 30 ID; unit test vẫn không thay thế monitor crawl dài | Thấp | Log + manifest trong T4.1 | A, D |
| R8 | Crawl dài: Windows có thể khóa file (antivirus) làm một cặp `failed`; ghi vào Drive qua FUSE không hợp với SQLite | Thấp | Pipeline ghi `failed` rồi tiếp tục, `run_crawl` chạy lại để thử lại; `colab_run.ipynb` giữ `data/` ở đĩa cục bộ và `rsync` sang Drive | A |
| R5 | Phiên bản thư viện chưa ghim (đang dùng khoảng phiên bản) | Thấp | Ghim sau pilot Colab | A |
| R6 | Đề chỉ cho phép mã + `requirements.txt` + `README.md` trong repo nộp. `STATUS.md`, `AGENTS.md`, `CONTRIBUTING.md` là file làm việc | TB | Gỡ ở T5.3, hoặc hỏi giảng viên trước khi nộp | Leader |
| R7 | Bảo vệ `main` / quyền thành viên — Phase 0 đóng; kiểm tra lại trước đóng băng nộp | Thấp | Leader rà soát T5.3 | Leader |

---

## 4. Nhật ký cho báo cáo

Chỉ ghi sự kiện **đưa được vào Jupyter Book**: cách thu thập, công cụ, số đo, hạn chế gặp khi lấy dữ liệu. Không ghi họp, phân vai, lịch nội bộ, hay lần sửa file làm việc. Dòng mới ở trên. Quyết định đầy đủ và bảng thăm dò `/src/` nằm ở mục 1.

| Ngày | Sự kiện |
| --- | --- |
| 29/10 | **arXiv 429 trên API discovery.** Trước đây một 429 ở `/api/query` dừng cả crawl (exit 3) dù `/src/` đã retry; sửa: chờ `Retry-After` tối đa 4 lần/chunk discovery (cùng `/src/`). Crawl 25k trên Colab ít bị dừng sớm khi IP bị throttle ngắn — vẫn exit 3 nếu 429 kéo dài sau 4 lần |
| 30/10 | **Colab — checkpoint sau resume.** Trước đây đếm vị trí trong list (kể ID đã backup) → mỗi ~1000 ID lại mirror cả `papers/` lên Drive (có thể >30 phút). Giờ: bỏ qua ID đã xong ở đầu list khi tính chu kỳ backup; file không đổi không copy lại |
| 29/10 | **Colab — sao lưu định kỳ khi crawl.** `run_crawl` mirror `data/` ra `CRAWLER_BACKUP_DIR` sau mỗi N ID cơ sở **mới** (`CRAWLER_BACKUP_EVERY_IDS`, notebook gợi ý **1000**); manifest snapshot SQLite an toàn khi crawl đang chạy. Giảm mất dữ liệu khi session Colab chết (vẫn nên Interrupt + rsync trước khi tắt) |
| 28/10 | **Colab pilot ~50 ID — arXiv 429 thoát mã 3.** Nguyên nhân thường gặp: bản cũ đếm 3 s từ header (đã sửa trên `main`) hoặc IP Colab bị throttle. Mã mới: chờ `Retry-After` trên `/src/` (4 lần) trước khi dừng; validate/export chỉ khi crawl xong — corpus dở sẽ báo thiếu `meta.json` / S2 |
| 28/10 | **Rate limit arXiv khi tải `/src/`.** Trước đây `HostClient.get(stream=True)` đánh dấu hết request ngay khi có header → request kế (phiên bản khác hoặc bài kế) có thể < 3 s sau khi body còn đang tải/đứt (`IncompleteRead`), dễ gặp **429** trên Colab. Sửa: chờ đến khi đóng stream; `download_source` retry lỗi body tạm thời trong `max_retries`. **182 passed** |
| 28/10 | **Pilot 30 ID (go/no-go Phase 1).** Một máy local, `--limit 30` đầu dải tháng 8/2024: **30/30** bài thành công, **57** phiên bản (**51** TeX ok, **6** PDF `no_source`), **0** `failed`, **0** `not_found`. V̄ **~1,9**; S2 **100 %** bài; **~33,6** ref/bài; **~78 %** ref đủ metadata; **~50 %** ref có arXiv ID. Byte giữ lại / nén thô (trung bình/pair): kept **~139 KB** vs raw archive **~5,7 MB** (~**2,0 %**). RAM peak **~39 MB** (pilot); đĩa corpus pilot **~8,4 MB**. S2 batch: **2** lô, **3×429**, **180 s** chờ retry. Ước chờ arXiv cho 25k (V̄ 1,9, 3 s/request `/src/`): **~40 h** — wall-clock phụ thuộc S2 và tải gói lớn |
| 28/10 | **Thiết kế S2 (mã).** Tham khảo S2 lấy bằng `POST /graph/v1/paper/batch` (tối đa 100 ID/lô, venue + refs lồng nhau); nếu `referenceCount` lớn hơn danh sách refs trả về thì fallback `GET …/references` với `limit=1000`. Limiter S2 mặc định 1,1 s/request, tăng khoảng cách sau 429, giảm dần sau 50 request thành công. `metrics.json` cộng dồn `s2.batches`, `s2.http_429`, `s2.retry_wait_seconds`. arXiv không đổi |
| 27/10 | **Cách thu thập đã cài (chưa chạy thật).** arXiv đọc bằng Atom API qua `requests` (không dùng `arxiv.py`) để API và `/src/` dùng chung một bộ giới hạn ≥ 3 giây. Ngày v2..vN lấy bằng `id_list=<id>v2,<id>v3,…` theo lô 50 (trường `updated` của từng bản). ID vắng trong lô được hỏi lại riêng một lần rồi mới ghi `not_found`. S2: một lần mỗi bài, phân trang 100; `venue` = `journal_ref`, nếu rỗng thì S2 (bỏ giá trị `arXiv.org`). Hai luồng (arXiv, S2) gặp nhau qua đĩa/manifest; resume bỏ qua cặp đã xong, thử lại `failed`. Khi arXiv trả 403/429 thì dừng cả lần chạy. Đo: thời gian chủ động theo giai đoạn (khám phá/tải/trích/S2) cộng dồn qua các lần resume, RSS đỉnh/trung bình gồm tiến trình con, đĩa đỉnh |
| 27/10 | **Khung báo cáo M1 (số mẫu).** Jupyter Book v1: trang tổng quan mô tả nguồn arXiv/S2, chờ ≥ 3 giây, `no_source` cho PDF, khác biệt bảng `papers` vs `references`; notebook thống kê từ CSV mẫu, ghi rõ chưa phải crawl. Số thật thay ở Phase 2 |
| 18/10 | **Phạm vi.** `src/id_ranges.csv`: 5 dải × 5.000 ID = **25.000**, tính cả hai đầu, không chồng. Tháng 8/2024: `01045–06044`, `06045–11044`, `11045–16044`. Tháng 7: `07839–12838`. Tháng 6: `07938–12937`. Chưa crawl |
| 18/10 | **Gói `/src/`** trên `export.arxiv.org`, cách nhau ≥ 3,5 giây, một kết nối, không lưu gói. tar.gz (`hep-th/9901001v1`, 15.221 byte): 1 `.tex` + 4 `.eps` — trích `.tex`/`.bib`, bỏ ảnh, giữ cây thư mục. `2408.11045v1` là tar.gz 10.199.199 byte, chưa liệt kê thành viên. PDF nằm trong dải được giao: `2408.01045v1`, 804.208 byte → `no_source`, vẫn giữ bản ghi. 404 `2408.00000v1` → `not_found`. Gzip một file và zip chưa gặp |
| 18/10 | **Siêu dữ liệu.** Hợp đồng khóa trong `models.py`. Khám phá theo lô 50 ID gốc (`id_list`); `published` = ngày v1; ngày bản trung gian lấy bằng `idvK` khi bài có từ 2 phiên bản. Chưa gọi arXiv API. Semantic Scholar: một lần mỗi bài trên ID gốc, phân trang `/references?offset=&limit=`; ngày dùng `publicationDate`, không có thì năm `YYYY`, không bịa ngày-tháng. Chưa có API key, chưa có số tham khảo |
| 18/10 | **Số liệu sách sẽ báo.** Bản không TeX = thư mục không có TeX + `meta.json`, không bỏ bài. Tỷ lệ tham khảo gồm ba số: (a) % bài có bản ghi S2, (b) % tham khảo đủ tiêu đề, tác giả và năm, (c) % tham khảo có arXiv ID. Kích thước trước/sau khi bỏ ảnh: byte gói chưa nén so với byte `.tex`+`.bib` giữ lại. Sách Jupyter Book v1; hình và bảng sinh lúc biên dịch. Chưa có số crawl, pilot, thời gian wall hay RAM |
| 17/10 | **Nguồn.** Chỉ `export.arxiv.org`: arXiv API (`id_list`) và `/src/{id}vN`. Semantic Scholar `paper/arXiv:{id}`. OAI-PMH và Kaggle không dùng để thu hoạch. Chờ ≥ 3 giây sau mỗi yêu cầu arXiv, một kết nối; S2 ≥ 1 yêu cầu/giây, mỗi máy một key. Chỉ giữ `.tex` và `.bib`; giữ lệnh hình trong TeX. Môi trường: Python 3.11 |
| 22/10 | **Cách chạy crawl (khuyến nghị GV).** Gom list ID (25.000), **một máy** crawl chung suốt M1–M3; subset Hugging Face **cuối M3** theo dải từng thành viên. Chi tiết D8 trong `Plans/milestone1-implementation-plan-vi.md` |
