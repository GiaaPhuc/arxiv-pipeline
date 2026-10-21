# STATUS — Milestone 1 (arXiv crawler)

> File sống, **nguồn sự thật duy nhất** về trạng thái dự án **so với đề** trong `project requirements/`.
> Agent đọc **mục 0** là đủ để nắm đề và chỗ nhóm đang đứng. Chỉ mở file đề khi cần câu chữ gốc, ví dụ mã trong đề, hoặc chi tiết chấm chưa nằm ở bảng dưới.
> Cập nhật mỗi khi xong việc / đổi quyết định / phát sinh rủi ro.
> Cập nhật lần cuối: **T4 21/10/2026, 23:35**
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
| Phạm vi crawl | Dải arXiv ID được giao; **mọi phiên bản** của từng bài. Không thu hoạch cả kho. OAI-PMH và Kaggle chỉ là hộp công cụ | 🟡 `src/id_ranges.csv`: 5 dải (tháng 6, 7, 8/2024), **25.000** ID, cả hai đầu. Chưa crawl |
| Nguồn và lịch sự | Chỉ `export.arxiv.org`, chờ ≥ 3 giây, một kết nối. Semantic Scholar `paper/arXiv:{id}`, ≥ 1 yêu cầu/giây; khóa API để khỏi bị điều tiết | 🟡 `http.py` T1.1: limiter, **403/429 trả caller (không retry client)**, retry 5xx, log không lộ key. Chưa có khóa S2 (R3) |
| Gói nguồn | Tải từng phiên bản qua `/src/{id}vN`. Chỉ giữ `.tex` và `.bib` bằng cách **trích**, không sửa nội dung. Giữ lệnh hình trong TeX. Bản không có TeX = bản ghi rỗng, không bỏ bài | 🟡 Đã gặp tar.gz (có `.tex` + `.eps`), PDF và 404 trên `export.arxiv.org`. T1.5 có tám nhóm fixture bắt buộc và sáu mẫu phụ, 14 kiểm tra pass; `source.py` còn stub |
| Siêu dữ liệu | Mỗi phiên bản: tiêu đề, tác giả, ngày nộp, ngày sửa, venue khi có. Mỗi tham khảo S2: tiêu đề, tác giả, ngày, arXiv ID nếu có, S2 ID. Giữ **mọi** tham khảo ở máy | 🟡 Hợp đồng khóa ở `src/crawler/models.py` (T0.1). `discovery.py`, `s2.py` stub |
| Hai bảng (nộp Hugging Face ở Milestone 3) | `papers`: arxiv_id, title, authors, submission_date, revised_dates, venue. `references`: chỉ tham khảo **có arXiv ID** — arxiv_id, ref_arxiv_id, ref_title, ref_authors, ref_submission_date, ref_s2_id | ⬜ `export.py` stub. Cột đã ghi trong `models.py`. `arxiv_id` bài trích dẫn lấy từ thư mục bài lúc xuất, không nằm trên từng `Reference` |
| Kho làm việc | Giữ cục bộ làm đầu vào Milestone 2 và 3. Gợi ý đề: một thư mục mỗi bài | 🟡 `manifest.py` xong T1.2 (SQLite + ghi nguyên tử). `corpus.py` stub. Bố cục `data/papers/...` đã khóa |
| Báo cáo | Jupyter Book: cách thu thập, thư viện, số bài thành công, tỷ lệ thành công, kích thước trung bình trước/sau khi bỏ ảnh, số tham khảo trung bình, tỷ lệ thành công siêu dữ liệu tham khảo. Thời gian wall và RAM trên Colab chỉ CPU. Hình và bảng sinh lúc biên dịch sách | ⬜ Repo sách chưa tạo (T1.4, vai E). Chốt dùng Jupyter Book v1 |
| Mã nguồn | Sạch, chạy được, có chú thích; trích dẫn nếu dùng tài liệu ngoài | 🟡 Có khung, T1.1/T1.2 (403/429 không retry) và fixture T1.5; 38 passed, 20 skipped. Các task crawl còn lại chưa có logic. Quy ước làm việc: `CONTRIBUTING.md` |
| Chấm (5 mục) | Báo cáo, mã, khám phá và tải đủ phiên bản, dữ liệu thu được, hiệu năng so với nhóm khác | ⬜ Chưa có số liệu |

### Việc đang làm (21/10)

- **Giai đoạn:** Phase 0 leader đã làm (18/10). Họp **T4 21/10** nói lại mục tiêu Milestone 1, phần leader đã làm, và trạng thái repo, rồi giao Phase 1. Không còn phiếu Phase 0. Phase 1 = code và pilot, hạn **trưa 25/10**. Phase 2 = crawl, sách, nộp, giao sau go/no-go trưa 25/10, đóng băng **sáng 4/11**, nộp **chiều 4/11**. Hạn Moodle **5/11** là hạn cứng. Crawl đủ bắt đầu **tối 25/10**. Kịch bản họp: `Leader/briefing-kickoff-21-10-vi.md`. Phiếu việc: `Leader/phase1-phan-cong-vi.md` (cả hai ngoài repo).
- **Mốc trong Phase 1** (không giao lại): hết 22/10 có `http.py`, `manifest.py`, `ids.py`, fixture, trang Pages giả; hết 23/10 có `discovery`, `source`; sáng 24/10 có `s2`; hết 24/10 có pipeline resume; sáng 25/10 pilot 30–50 ID.
- **Đã có:** khung repo, `config.py`, `models.py` (hợp đồng đã khóa), `src/id_ranges.csv` (5 dải, 25.000 ID), `CONTRIBUTING.md`, CLI, fixture T1.5, test khung và 14 kiểm tra fixture, ruff/pytest/pre-commit/CI.
- **Chưa có logic:** `ids`, `discovery`, `source`, `s2`, `metrics`, `pipeline`, `export`, `corpus`, `validate_corpus` (đều ném `NotImplementedError("T…")`). **`http.py` và `manifest.py` đã có logic (T1.1, T1.2).**
- **Test:** `pytest` 21/10 sau merge `main` → **38 passed, 20 skipped (0,84 s)**. `ruff check` sạch, `ruff format --check` sạch (33 file).
- **Bị chặn / còn bấm tay:** A là leader (19/10). Chưa gán tên B–E và chưa gán dải (R2). Chưa có API key S2 (R3). Chưa mời thành viên, chưa bảo vệ `main`, chưa xác nhận Actions trên GitHub (máy này không có `gh`).
- **Việc kế tiếp:** (1) họp 21/10 giao cả Phase 1 theo phiếu; (2) code từ `http`/`ids`/fixture đến pilot, cùng một hạn trưa 25/10; (3) B xin key S2 của B trong Phase 1, D gửi link trang đăng ký, trước tối 25/10.
- **Vai → module:** A (leader) `http` `manifest` `pipeline` `metrics` · B `ids` `discovery` `s2` · C `source` + fixture · D `export` `corpus` + notebook số liệu · E `validate_corpus` + sách (repo riêng). A, B, C viết crawler. D và E vẫn mỗi người một dải và một commit.
- **Ai đợi ai trong Phase 1:** B và C làm bằng test giả, không đợi `http.py`. B làm `s2` sau `discovery` của chính B. D (`export`, `corpus`) và E (`validate_corpus`) mỗi người một thư mục mẫu, không đợi crawl và không đợi nhau. A đợi PR `discovery` / `source` / `s2` xanh rồi mới ghép `pipeline` (hết 24/10; `s2` hạn sáng 24/10). Pilot sáng 25/10 đợi pipeline chạy lại được. Năm bài S2 thật đợi key của B.

---

## 1. Quyết định đã chốt

| Ngày | Quyết định |
| --- | --- |
| 17/10 | Nguồn dữ liệu: arXiv API (`id_list`) + `/src/{id}vN` trên `export.arxiv.org` + S2 Graph API. Pipeline resumable bằng SQLite manifest; hai worker theo host (≥ 3 s arXiv, ≥ 1 s S2); chỉ giữ `.tex`/`.bib`, giữ cấu trúc thư mục; lưu JSON thô trước |
| 17/10 | Dải ID = 4 trường `start_month,start_id,end_month,end_id`, tính cả hai đầu. ID không tồn tại ghi `not_found`, tách khỏi `failed` |
| 17/10 | Công cụ: Python 3.11, `ruff==0.17.1`, `pytest`, `pre-commit`. Nhánh `m1/<vai>-<mô-tả>`; một PR một reviewer không phải owner; không đẩy thẳng `main` |
| 18/10 | Dời kickoff sang T4 21/10. Hạn Moodle 5/11 là hạn cứng. Nộp mục tiêu chiều 4/11, đóng băng sáng 4/11, crawl đủ tối 25/10 |
| 18/10 | **T0.1.** Khóa `models.py` theo đề. Không đổi trường (`SCHEMA_VERSION` vẫn 1). Họp 21/10 thông báo, không biểu quyết lại. Đổi sau này bằng PR riêng, cả 5 người thấy |
| 18/10 | **T0.2.** Theo đề và API manual mà đề trích dẫn, chưa gọi API. Lô `id_list` = 50 ID gốc. Hậu tố bản mới nhất = số phiên bản. `published` = ngày v1. `updated` của lần gọi gốc = ngày bản mới nhất; ngày bản trung gian lấy bằng `idvK` khi có từ 2 phiên bản. ID vắng mặt sau một lần gọi riêng = `not_found`. Lỗi mạng hết lần thử = `failed`. ID cũ giữ nguyên văn |
| 18/10 | **T0.3.** Đã gọi `export.arxiv.org/src/`, cách nhau ≥ 3,5 giây, một kết nối, không lưu gói. Xem mã HTTP trước magic bytes. tar.gz `1f 8b` + `ustar` thì trích `.tex`/`.bib`. PDF = `no_source`. 404 = `not_found`. Gzip đơn và zip chưa gặp; fixture vẫn dựng giả |
| 18/10 | **T0.4.** Một lần S2 mỗi bài trên ID gốc, `s2_scope: "paper"`. Phân trang bằng `/references?offset=&limit=`. Ngày: `publicationDate`, không có thì `YYYY`, không bịa ngày-tháng. Mỗi Colab một API key. Chưa có key |
| 18/10 | **T0.5.** Chưa hỏi giảng viên. Giữ phương án tạm, không chặn code: chờ 3 giây từ lúc yêu cầu trước kết thúc; bản ghi rỗng = thư mục không TeX + `meta.json`; tỷ lệ tham khảo báo cả (a) % bài có S2, (b) % tham khảo đủ tiêu đề/tác giả/năm, (c) % tham khảo có arXiv ID; sách dùng Jupyter Book v1 |
| 18/10 | **T0.7.** File thực tế có **5 dải**, không phải 20. Mỗi dải 5.000 ID, tổng **25.000**. Mỗi dải nằm trong một tháng, không chồng: tháng 8 `01045–06044`, `06045–11044`, `11045–16044`; tháng 7 `07839–12838`; tháng 6 `07938–12937`. Mỗi người một dải, điền tên ở họp 21/10 |
| 19/10 | Giao việc còn hai phase sau Phase 0. Phase 1: code + pilot, hạn trưa 25/10. Phase 2: crawl + sách + nộp, đóng băng sáng 4/11. Mã T1–T5 giữ nguyên. Ngày 22, 23, 24 và 1/11 là mốc trong phase |
| 19/10 | Họp 21/10 nói lại Phase 0 (leader đã làm), không giao spike. Phiếu Phase 1: B/C/D/E không đợi `http.py`; A đợi ba PR rồi mới ghép pipeline; pilot đợi pipeline. Bỏ phiếu `Leader/phase0-phan-cong-vi.md` |
| 19/10 | Vai A là leader. Họp 21/10 gán B–E, năm dải, và người dự phòng Moodle (một người trong B–E, ưu tiên E) |
| 21/10 | **T1.1 / pilot.** `HostClient` **không** retry 403/429; trả caller để pipeline dừng và báo nhóm (`CONTRIBUTING.md`). Retry client chỉ cho 5xx tạm thời. Phản hồi reviewer C |
| 19/10 | Ba người viết crawler. A: `http`, `manifest`, `pipeline`, `metrics`. B: `ids`, `discovery`, `s2`. C: fixture, `source`. D: `export`, `corpus`, notebook số liệu, gửi link trang xin key. E: sách và `validate_corpus`. D và E vẫn mỗi người một dải crawl và một commit. Test giả `s2` hạn sáng 24/10. Trễ thì A pair với B trong ngày 24/10 |

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

### Phase 0 — chốt 18/10, họp 21/10 chỉ thông báo

| Mã | Việc | Owner | TT | Ghi chú |
| --- | --- | --- | --- | --- |
| T0.1 | Hợp đồng dữ liệu | Leader | ✅ | `models.py` khóa 18/10. Không đổi schema |
| T0.2 | Spike arXiv API | Leader | ✅ | Quyết định theo đề, chưa gọi API. Lô 50. Kiểm tra 10 ID thật là việc T2.1 |
| T0.3 | Spike `/src/` | Leader | ✅ | Bảng ở mục 1. PDF nằm trong dải được giao |
| T0.4 | Spike S2 + xin API key | Leader / B | 🟡 | Bốn câu đã chốt. Key chưa có (R3). B xin trong Phase 1; D gửi link trang đăng ký |
| T0.5 | Câu còn mở với giảng viên | Leader | ✅ | Chưa hỏi. Phương án tạm thành quy ước, không chặn code |
| T0.6 | Repo, lint, quy ước PR | Leader | 🟡 | `CONTRIBUTING.md` đã có. Còn bấm trên GitHub: mời Write, bảo vệ `main`, xác nhận Actions. Repo sách là T1.4 |
| T0.7 | File dải + tổng số ID | Leader | ✅ | `src/id_ranges.csv`: 5×5.000 = 25.000. Không bịa thêm 15 dải |

### Phase 1 — giao họp 21/10, hạn trưa CN 25/10

Một phiếu: nền tảng, module, tích hợp, pilot. Phiếu: `Leader/phase1-phan-cong-vi.md`. Tên A–E điền trong họp 21/10. Cột mốc là thứ tự, không phải lần giao mới.

B và C test bằng HTTP giả, không đợi `http.py`. B làm `s2` sau `discovery`. D và E mỗi người một thư mục mẫu cho T3.2, không đợi nhau. A đợi ba PR `discovery` / `source` / `s2` rồi mới làm T3.1. `s2` hạn sáng 24/10. Pilot (T3.3) đợi T3.1. Năm bài S2 thật đợi key của B.

| Mã | Việc | Owner | Mốc | TT |
| --- | --- | --- | --- | --- |
| T1.1 | `http.py` (RateLimiter; 403/429 stop; retry 5xx) | A | hết 22/10 | ✅ |
| T1.2 | `manifest.py` + ghi nguyên tử (`config.py` đã xong) | A | hết 22/10 | ✅ |
| T1.3 | `ids.py` | B | hết 22/10 | ⬜ stub |
| T1.4 | Khung Jupyter Book v1 + Pages | E | hết 22/10 | ⬜ |
| T1.5 | Fixture tổng hợp (`tests/conftest.py`) | C (Quỳnh) | hết 22/10 | ✅ Tám nhóm bắt buộc + sáu mẫu phụ; 14 kiểm tra pass trong worktree sạch |
| T2.1 | `discovery.py` (test giả; 10 ID thật ghi trên PR, không đợi `http.py`) | B | hết 23/10 | ⬜ stub |
| T2.2 | `source.py` (fixture + client giả, không đợi `http.py`) | C | hết 23/10 | ⬜ stub |
| T2.3 | `s2.py` test giả sáng 24/10; 5 bài thật đợi key của B, trước trưa 25/10 | B | sáng 24/10 | ⬜ stub |
| T2.4 | Đo mỏng trong pipeline (`metrics.py`) | A | hết 24/10 | ⬜ stub |
| T2.5 | Notebook thống kê trên số giả / pilot. E nhúng vào sách | D | trưa 25/10 | ⬜ |
| T3.1 | `pipeline.py` (đợi PR discovery, source, s2 xanh) | A | hết 24/10 | ⬜ stub |
| T3.2 | `export.py` + `corpus.py` (D) và `validate_corpus.py` (E), mỗi người một thư mục mẫu | D, E | hết 24/10 | ⬜ stub |
| T3.3 | Pilot 30–50 ID trên Colab (đợi pipeline resume) | A + cả nhóm | trưa 25/10 | ⬜ |
| T3.4 | Review chéo, lint sạch | Cả nhóm | trưa 25/10 | ⬜ |

### Phase 2 — giao sau go/no-go trưa 25/10, đóng băng sáng 4/11

| Mã | Việc | Mốc | TT |
| --- | --- | --- | --- |
| T4.1 | Crawl đủ, mỗi người một dải; A ghép | 29/10 | ⬜ |
| T4.2 | Chạy lại `failed` | 1/11 | ⬜ |
| T4.3 | Validate + đối chiếu ≥ 20 phiên bản | 1/11 | ⬜ |
| T4.4 | `stats/` vào repo sách | 1/11 | ⬜ |
| T5.1 | Sách (viết nháp trong lúc crawl) | 3/11 | ⬜ |
| T5.2 | README | 3/11 | ⬜ (có bản khởi tạo) |
| T5.3 | Đóng băng checklist | sáng 4/11 | ⬜ |
| T5.4 | Nộp Moodle. Hạn cứng 5/11 | chiều 4/11 | ⬜ |

---

## 3. Rủi ro và vấn đề đang mở

| # | Mô tả | Mức | Hành động | Owner |
| --- | --- | --- | --- | --- |
| R1 | 25.000 ID, 5 dải bằng nhau. Chưa biết số phiên bản trung bình. Một máy, nếu khoảng 2 phiên bản, phần chờ arXiv cỡ 42 giờ cộng thời gian tải. Năm máy, mỗi người một dải, kịp nếu crawl bật tối 25/10 và Colab không bị ngắt | TB | Pilot sáng 25/10 ra số giờ thật rồi mới bấm crawl cùng ngày. Không dồn 25.000 ID vào một Colab | A |
| R2 | A là leader (19/10). Chưa gán tên B–E và chưa gán 5 dải. Đường găng code đi qua A và qua B (`discovery` rồi `s2`) | TB | Gán B–E và dải ở họp 21/10. Người dự phòng Moodle ưu tiên E. `s2` trễ sáng 24/10 thì A pair với B trong ngày | Leader |
| R3 | Chưa có API key S2. Năm máy dùng chung một key sẽ vượt 1 yêu cầu/giây | TB | Mỗi người một key miễn phí trước tối 25/10. B xin key của B trước (5 bài thật). D chỉ gửi trang đăng ký, không gửi giá trị key | B, D |
| R4 | Actions trên GitHub chưa được xác nhận xanh từ máy này (`gh` không có) | Thấp | Leader mở tab Actions trước khi ai tạo nhánh | Leader |
| R5 | Phiên bản thư viện chưa ghim (đang dùng khoảng phiên bản) | Thấp | Ghim sau pilot Colab | A |
| R6 | Đề chỉ cho phép mã + `requirements.txt` + `README.md` trong repo nộp. `STATUS.md`, `AGENTS.md`, `CONTRIBUTING.md` là file làm việc | TB | Gỡ ở T5.3, hoặc hỏi giảng viên trước khi nộp | Leader |
| R7 | `main` chưa khóa đẩy thẳng và thành viên chưa được mời | TB | Ba bước trong `CONTRIBUTING.md`, bấm trên GitHub trước họp hoặc ngay đầu họp | Leader |

---

## 4. Nhật ký cho báo cáo

Chỉ ghi sự kiện **đưa được vào Jupyter Book**: cách thu thập, công cụ, số đo, hạn chế gặp khi lấy dữ liệu. Không ghi họp, phân vai, lịch nội bộ, hay lần sửa file làm việc. Dòng mới ở trên. Quyết định đầy đủ và bảng thăm dò `/src/` nằm ở mục 1.

| Ngày | Sự kiện |
| --- | --- |
| 18/10 | **Phạm vi.** `src/id_ranges.csv`: 5 dải × 5.000 ID = **25.000**, tính cả hai đầu, không chồng. Tháng 8/2024: `01045–06044`, `06045–11044`, `11045–16044`. Tháng 7: `07839–12838`. Tháng 6: `07938–12937`. Chưa crawl |
| 18/10 | **Gói `/src/`** trên `export.arxiv.org`, cách nhau ≥ 3,5 giây, một kết nối, không lưu gói. tar.gz (`hep-th/9901001v1`, 15.221 byte): 1 `.tex` + 4 `.eps` — trích `.tex`/`.bib`, bỏ ảnh, giữ cây thư mục. `2408.11045v1` là tar.gz 10.199.199 byte, chưa liệt kê thành viên. PDF nằm trong dải được giao: `2408.01045v1`, 804.208 byte → `no_source`, vẫn giữ bản ghi. 404 `2408.00000v1` → `not_found`. Gzip một file và zip chưa gặp |
| 18/10 | **Siêu dữ liệu.** Hợp đồng khóa trong `models.py`. Khám phá theo lô 50 ID gốc (`id_list`); `published` = ngày v1; ngày bản trung gian lấy bằng `idvK` khi bài có từ 2 phiên bản. Chưa gọi arXiv API. Semantic Scholar: một lần mỗi bài trên ID gốc, phân trang `/references?offset=&limit=`; ngày dùng `publicationDate`, không có thì năm `YYYY`, không bịa ngày-tháng. Chưa có API key, chưa có số tham khảo |
| 18/10 | **Số liệu sách sẽ báo.** Bản không TeX = thư mục không có TeX + `meta.json`, không bỏ bài. Tỷ lệ tham khảo gồm ba số: (a) % bài có bản ghi S2, (b) % tham khảo đủ tiêu đề, tác giả và năm, (c) % tham khảo có arXiv ID. Kích thước trước/sau khi bỏ ảnh: byte gói chưa nén so với byte `.tex`+`.bib` giữ lại. Sách Jupyter Book v1; hình và bảng sinh lúc biên dịch. Chưa có số crawl, pilot, thời gian wall hay RAM |
| 17/10 | **Nguồn.** Chỉ `export.arxiv.org`: arXiv API (`id_list`) và `/src/{id}vN`. Semantic Scholar `paper/arXiv:{id}`. OAI-PMH và Kaggle không dùng để thu hoạch. Chờ ≥ 3 giây sau mỗi yêu cầu arXiv, một kết nối; S2 ≥ 1 yêu cầu/giây, mỗi máy một key. Chỉ giữ `.tex` và `.bib`; giữ lệnh hình trong TeX. Môi trường: Python 3.11 |
