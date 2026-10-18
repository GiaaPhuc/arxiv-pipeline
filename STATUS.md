# STATUS — Milestone 1 (arXiv crawler)

> File sống, **nguồn sự thật duy nhất** về trạng thái dự án **so với đề** trong `project requirements/`.
> Agent đọc **mục 0** là đủ để nắm đề và chỗ nhóm đang đứng. Chỉ mở file đề khi cần câu chữ gốc, ví dụ mã trong đề, hoặc chi tiết chấm chưa nằm ở bảng dưới.
> Cập nhật mỗi khi xong việc / đổi quyết định / phát sinh rủi ro.
> Cập nhật lần cuối: **CN 18/10/2026, 12:40**
> Ký hiệu: ✅ xong · 🟡 đang làm / một phần · ⬜ chưa bắt đầu · ⛔ bị chặn
> Mã task (T0.1, T1.3...) lấy từ kế hoạch kỹ thuật + playbook của nhóm (chia sẻ riêng, không nằm trong repo). Điều kiện xong của từng module nằm ngay trong docstring đầu file module đó.

---

## 0. Đọc nhanh (agent / người mới chỉ cần đọc phần này)

### Đề và phạm vi

- Môn Nhập môn Khoa học Dữ liệu, dự án nhóm về arXiv. Ba milestone cộng dồn vào **một repo mã**. Hiện **chỉ làm Milestone 1** (thu thập). Milestone 2 (parse LaTeX thành cây, chuẩn hóa, `\bibitem` → BibTeX, bỏ trùng) và Milestone 3 (đối sánh BibTeX với bảng `references`, MRR@5, poster A2 dọc, dataset Hugging Face) **chưa bắt đầu** — không triển khai.
- Nộp Milestone 1 qua Moodle: link repo mã công khai + link Jupyter Book (repo sách riêng, GitHub Pages). **Không nộp dữ liệu** ở mốc này. Viết bằng tiếng Việt. Không nộp trễ.
- Khi nộp, repo mã chỉ được chứa mã (`.py` / `.ipynb`), `requirements.txt` và `README.md`. `STATUS.md`, `AGENTS.md`, `CONTRIBUTING.md`, `plans/`, `project requirements/` đang ở trong repo để làm việc — xử lý lúc đóng băng (R6, T5.3).

### Milestone 1 — đề vs hiện trạng

| Hạng mục đề | Đề yêu cầu (rút gọn) | Hiện trạng |
| --- | --- | --- |
| Phạm vi crawl | Dải arXiv ID được giao; **mọi phiên bản** của từng bài. Không thu hoạch cả kho. OAI-PMH và Kaggle chỉ là hộp công cụ | 🟡 `src/id_ranges.csv`: 5 dải cùng tháng, **25.000** ID, cả hai đầu. Chưa crawl |
| Nguồn và lịch sự | Chỉ `export.arxiv.org`, chờ ≥ 3 giây, một kết nối. Semantic Scholar `paper/arXiv:{id}`, ≥ 1 yêu cầu/giây; khóa API để khỏi bị điều tiết | 🟡 Quy ước đã chốt. `http.py` còn stub. Chưa có khóa S2 (R3) |
| Gói nguồn | Tải từng phiên bản qua `/src/{id}vN`. Chỉ giữ `.tex` và `.bib` bằng cách **trích**, không sửa nội dung. Giữ lệnh hình trong TeX. Bản không có TeX = bản ghi rỗng, không bỏ bài | 🟡 Đã gặp tar.gz (có `.tex` + `.eps`), PDF và 404 trên `export.arxiv.org`. `source.py` vẫn stub |
| Siêu dữ liệu | Mỗi phiên bản: tiêu đề, tác giả, ngày nộp, ngày sửa, venue khi có. Mỗi tham khảo S2: tiêu đề, tác giả, ngày, arXiv ID nếu có, S2 ID. Giữ **mọi** tham khảo ở máy | 🟡 Hợp đồng khóa ở `src/crawler/models.py` (T0.1). `discovery.py`, `s2.py` stub |
| Hai bảng (nộp Hugging Face ở Milestone 3) | `papers`: arxiv_id, title, authors, submission_date, revised_dates, venue. `references`: chỉ tham khảo **có arXiv ID** — arxiv_id, ref_arxiv_id, ref_title, ref_authors, ref_submission_date, ref_s2_id | ⬜ `export.py` stub. Cột đã ghi trong `models.py`. `arxiv_id` bài trích dẫn lấy từ thư mục bài lúc xuất, không nằm trên từng `Reference` |
| Kho làm việc | Giữ cục bộ làm đầu vào Milestone 2 và 3. Gợi ý đề: một thư mục mỗi bài | ⬜ `corpus.py` stub. Bố cục `data/papers/...` đã khóa. Thêm SQLite manifest |
| Báo cáo | Jupyter Book: cách thu thập, thư viện, số bài thành công, tỷ lệ thành công, kích thước trung bình trước/sau khi bỏ ảnh, số tham khảo trung bình, tỷ lệ thành công siêu dữ liệu tham khảo. Thời gian wall và RAM trên Colab chỉ CPU. Hình và bảng sinh lúc biên dịch sách | ⬜ Repo sách chưa tạo (T1.4, vai E). Chốt dùng Jupyter Book v1 |
| Mã nguồn | Sạch, chạy được, có chú thích; trích dẫn nếu dùng tài liệu ngoài | 🟡 Có khung và test. Chưa có logic crawl. Quy ước làm việc: `CONTRIBUTING.md` |
| Chấm (5 mục) | Báo cáo, mã, khám phá và tải đủ phiên bản, dữ liệu thu được, hiệu năng so với nhóm khác | ⬜ Chưa có số liệu |

### Việc đang làm (18/10)

- **Giai đoạn:** Phase 0 đã chốt phần quyết định và file (18/10). Kickoff **T4 21/10** chỉ thông báo phần này rồi giao Phase 1. Phiếu nói trong họp: `Leader/phase1-phan-cong-vi.md` (ngoài repo). Nộp mục tiêu **chiều 4/11**. Hạn Moodle **5/11** là hạn cứng. Crawl đủ bắt đầu **tối 25/10**. Đóng băng sáng 4/11.
- **Mốc kế tiếp:** họp 21/10 điền tên A–E và người nhận từng dải. Hết 22/10 phải có `http.py`, `manifest.py`, `ids.py`, fixture, và trang Pages giả.
- **Đã có:** khung repo, `config.py`, `models.py` (hợp đồng đã khóa), `src/id_ranges.csv` (5 dải, 25.000 ID), `CONTRIBUTING.md`, CLI, test khung, ruff/pytest/pre-commit/CI.
- **Chưa có logic:** `http`, `manifest`, `ids`, `discovery`, `source`, `s2`, `metrics`, `pipeline`, `export`, `corpus`, `validate_corpus` (đều ném `NotImplementedError("T…")`).
- **Test:** `pytest` 18/10 12:40 → **17 passed, 26 skipped**. `ruff check` và `ruff format --check` sạch.
- **Bị chặn / còn bấm tay:** chưa gán tên A–E (R2); chưa có API key S2 (R3); chưa mời thành viên, chưa bảo vệ `main`, chưa xác nhận Actions trên GitHub (máy này không có `gh`).
- **Việc kế tiếp:** (1) họp 21/10 giao Phase 1 theo phiếu; (2) ngày 22 code `http`, `manifest`, `ids`, fixture, khung sách; (3) D xin key và viết test S2 giả lập.
- **Vai → module:** A `http` `manifest` `pipeline` `metrics` · B `ids` `discovery` · C `source` + fixture · D `s2` `export` `corpus` · E `validate_corpus` + báo cáo (repo sách riêng).

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
| 18/10 | **T0.7.** File thực tế có **5 dải**, không phải 20. Mỗi dải 5.000 ID, tổng **25.000**. Cùng tháng, không chồng: tháng 8 là `01045–06044`, `06045–11044`, `11045–16044`. Mỗi người một dải, điền tên ở họp 21/10 |

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
| T0.4 | Spike S2 + xin API key | Leader / D | 🟡 | Bốn câu đã chốt. Key chưa có (R3). D xin trước 23/10 |
| T0.5 | Câu còn mở với giảng viên | Leader | ✅ | Chưa hỏi. Phương án tạm thành quy ước, không chặn code |
| T0.6 | Repo, lint, quy ước PR | Leader | 🟡 | `CONTRIBUTING.md` đã có. Còn bấm trên GitHub: mời Write, bảo vệ `main`, xác nhận Actions. Repo sách là T1.4 |
| T0.7 | File dải + tổng số ID | Leader | ✅ | `src/id_ranges.csv`: 5×5.000 = 25.000. Không bịa thêm 15 dải |

### Phase 1 — hạn hết T5 22/10

Phiếu giao việc: `Leader/phase1-phan-cong-vi.md`. Tên A–E điền trong họp 21/10.

| Mã | Việc | Owner | TT |
| --- | --- | --- | --- |
| T1.1 | `http.py` (RateLimiter, retry) | A | ⬜ stub |
| T1.2 | `manifest.py` + ghi nguyên tử (`config.py` đã xong) | A | ⬜ stub |
| T1.3 | `ids.py` | B | ⬜ stub |
| T1.4 | Khung Jupyter Book v1 + Pages | E | ⬜ |
| T1.5 | Fixture tổng hợp (`tests/conftest.py`) | C | ⬜ |
| — | D ngày 22: xin key + test S2 giả lập. Chưa gọi bài thật | D | ⬜ |

### Phase 2–5

| Phase | Task | TT |
| --- | --- | --- |
| 2 (hạn T6 23/10) | T2.1 `discovery` · T2.2 `source` · T2.3 `s2`. T2.4 đo mỏng trong pipeline (hạn 24/10). T2.5 sách viết trong lúc crawl | ⬜ |
| 3 (hạn trưa 25/10) | T3.1 `pipeline` · T3.2 `export`/`corpus`/`validate` · T3.3 pilot 30–50 ID · review trong 12 giờ | ⬜ |
| 4 (tối 25/10–1/11) | T4.1 crawl đủ · T4.2 chạy lại `failed` · T4.3 validate · T4.4 `stats/` | ⬜ |
| 5 (2–4/11) | T5.1 sách · T5.2 README · T5.3 đóng băng sáng 4/11 · T5.4 nộp Moodle chiều 4/11. Hạn cứng 5/11 | ⬜ (README có bản khởi tạo) |

---

## 3. Rủi ro và vấn đề đang mở

| # | Mô tả | Mức | Hành động | Owner |
| --- | --- | --- | --- | --- |
| R1 | 25.000 ID, 5 dải bằng nhau. Chưa biết số phiên bản trung bình. Một máy, nếu khoảng 2 phiên bản, phần chờ arXiv cỡ 42 giờ cộng thời gian tải. Năm máy, mỗi người một dải, kịp nếu crawl bật tối 25/10 và Colab không bị ngắt | TB | Pilot sáng 25/10 ra số giờ thật rồi mới bấm crawl cùng ngày. Không dồn 25.000 ID vào một Colab | A |
| R2 | Chưa gán tên vai A–E; đường găng đi qua A | TB | Gán ở họp 21/10 | Leader |
| R3 | Chưa có API key S2. Năm máy dùng chung một key sẽ vượt 1 yêu cầu/giây | TB | Mỗi người một key miễn phí trước tối 25/10. D xin trước và chỉ gửi trang đăng ký, không gửi giá trị key | D |
| R4 | Actions trên GitHub chưa được xác nhận xanh từ máy này (`gh` không có) | Thấp | Leader mở tab Actions trước khi ai tạo nhánh | Leader |
| R5 | Phiên bản thư viện chưa ghim (đang dùng khoảng phiên bản) | Thấp | Ghim sau pilot Colab | A |
| R6 | Đề chỉ cho phép mã + `requirements.txt` + `README.md` trong repo nộp. `STATUS.md`, `AGENTS.md`, `CONTRIBUTING.md` là file làm việc | TB | Gỡ ở T5.3, hoặc hỏi giảng viên trước khi nộp | Leader |
| R7 | `main` chưa khóa đẩy thẳng và thành viên chưa được mời | TB | Ba bước trong `CONTRIBUTING.md`, bấm trên GitHub trước họp hoặc ngay đầu họp | Leader |

---

## 4. Nhật ký thay đổi

Dòng mới nhất ở trên cùng.

| Ngày giờ | Thay đổi |
| --- | --- |
| 18/10 12:40 | Phase 0: khóa `models.py`; chốt T0.2/T0.4/T0.5 theo đề; spike `/src/` (tar.gz, PDF trong dải, 404); thêm `src/id_ranges.csv` (25.000 ID) và `CONTRIBUTING.md`. `pytest` 17 pass / 26 skip. Phiếu Phase 1 nằm ngoài repo |
| 18/10 | Dời kickoff sang T4 21/10. Hạn Moodle 5/11 là hạn cứng. Nộp mục tiêu chiều 4/11, đóng băng sáng 4/11, crawl đủ tối 25/10. Cập nhật playbook, briefing, phiếu Phase 0 và đoạn lịch ở kế hoạch kỹ thuật |
| 17/10 18:56 | Mục 0 thêm bảng đề Milestone 1 so với hiện trạng, để agent không đọc lại `project requirements/` mỗi lần được hỏi |
| 17/10 18:50 | Thêm phiếu phân công Phase 0 sau họp (`Leader/phase0-phan-cong-vi.md`). Tên A–E còn trống |
| 17/10 16:50 | Xác nhận `main` trùng `origin/main` (khung đã đẩy, không commit thêm). T0.6 còn mời người, bảo vệ nhánh, xác nhận Actions. Briefing kickoff nằm ngoài repo, thư mục `Leader/` |
| 17/10 15:30 | CI đỏ vì ruff format đụng code mẫu trong `Requirements/milestone1-data-crawling-vi.md`. Loại `Requirements/` và `Plans/` khỏi ruff trong `pyproject.toml` |
| 17/10 14:40 | Nén lịch Milestone 1 còn 10 ngày trong playbook §2 và kế hoạch kỹ thuật: nộp mục tiêu chiều 26/10, crawl tối 21/10, hạn 5/11 là đệm. Làm mỏng spike, pilot, metrics, sách |
| 17/10 14:00 | Chuyển file trạng thái vào repo thành `STATUS.md` (nguồn duy nhất), thêm phần "Đọc nhanh"; thêm `AGENTS.md` hướng dẫn agent đọc/cập nhật file này. Thêm rủi ro R6 |
| 17/10 13:55 | Tạo file trạng thái (bản đầu, ở thư mục `Plans/`). Xác nhận repo mã còn ở `Initial commit`; `pytest` 17 pass / 26 skip |
| 17/10 ~13:45 | Dựng khung repo mã: 11 module stub kèm owner/task/điều kiện xong, `models.py` nháp hợp đồng, `config.py`, CLI, `corpus.py`, `validate_corpus.py`, `colab_run.ipynb`, test khung, `pyproject.toml`, pre-commit, CI, mẫu PR, README. Ghim `ruff==0.17.1` |
