# STATUS — Milestone 1 (arXiv crawler)

> File sống, **nguồn sự thật duy nhất** về trạng thái dự án. Cập nhật mỗi khi xong việc / đổi quyết định / phát sinh rủi ro.
> Cập nhật lần cuối: **T7 17/10/2026, 15:30**
> Ký hiệu: ✅ xong · 🟡 đang làm / một phần · ⬜ chưa bắt đầu · ⛔ bị chặn
> Mã task (T0.1, T1.3...) lấy từ kế hoạch kỹ thuật + playbook của nhóm (chia sẻ riêng, không nằm trong repo). Điều kiện xong của từng module nằm ngay trong docstring đầu file module đó.

---

## 0. Đọc nhanh (agent / người mới chỉ cần đọc phần này)

- **Giai đoạn:** Phase 0 (khởi động), lịch đã nén còn **10 ngày**. Nộp mục tiêu **chiều 26/10**. Hạn Moodle giả định **5/11** chỉ là đệm. Crawl đủ bắt đầu **tối 21/10**. Đóng băng sáng 26/10.
- **Mốc kế tiếp:** tối 17/10 — gán A–E, duyệt `src/crawler/models.py` trên chat, đủ 20 dải, spike tối đa 2 giờ. Ngày 18 là code (`http`, `ids`, fixture), không còn ngày viết tài liệu.
- **Đã có:** khung repo (module stub + owner/task trong docstring), `config.py`, `models.py` (bản nháp hợp đồng), CLI `run_crawl.py`, test khung, ruff/pytest/pre-commit/CI.
- **Chưa có logic:** `http`, `manifest`, `ids`, `discovery`, `source`, `s2`, `metrics`, `pipeline`, `export`, `corpus`, `validate_corpus` (đều ném `NotImplementedError("T…")`).
- **Test:** `pytest` → 17 pass, 26 skip (skip = ca test owner phải viết).
- **Bị chặn / rủi ro chính:** chưa gán tên A–E; chưa đủ 20 dải nên chưa biết tổng số ID; chưa có API key S2; CI chưa chạy trên GitHub.
- **Việc kế tiếp:** (1) gán vai A–E + thu 20 dải tối nay; (2) spike 2 giờ T0.2/T0.3/T0.4 rồi mở nhánh code luôn; (3) duyệt `models.py` trên chat, không viết hợp đồng riêng; (4) hết 18/10 phải có `http.py`, `ids.py`, fixture.
- **Vai → module:** A `http` `manifest` `pipeline` `metrics` · B `ids` `discovery` · C `source` + fixture · D `s2` `export` `corpus` · E `validate_corpus` + báo cáo (repo sách riêng).

---

## 1. Quyết định đã chốt

| Ngày | Quyết định |
| --- | --- |
| 17/10 | Nguồn dữ liệu: arXiv API (`id_list`) + `/src/{id}vN` trên `export.arxiv.org` + S2 Graph API. Pipeline resumable bằng SQLite manifest; hai worker theo host (≥ 3 s arXiv, ≥ 1 s S2); chỉ giữ `.tex`/`.bib`, giữ cấu trúc thư mục; lưu JSON thô trước |
| 17/10 | Dải ID = 4 trường `start_month,start_id,end_month,end_id`, tính cả hai đầu. Mỗi người 4 dải, cả nhóm 20 dải. Ví dụ `2024-08`/`11045`→`16044` = `2408.11045`…`2408.16044` = 5.000 ID |
| 17/10 | ID không tồn tại ghi `not_found`, tách khỏi `failed` khi tính tỷ lệ thành công |
| 17/10 | Mỗi người crawl 4 dải của mình trên Colab riêng; A ghép manifest |
| 17/10 | Công cụ: Python 3.11, `ruff==0.17.1`, `pytest`, `pre-commit`. Nhánh `m1/<vai>-<mô-tả>`; một PR một reviewer không phải owner; không đẩy thẳng `main` |
| 17/10 | Lịch làm việc nén còn 10 ngày (17–26/10). Giữ D1–D5. Làm mỏng: spike 2 giờ, pilot 30–50 ID, đo hiệu năng trong pipeline, sách 3 file viết lúc crawl. Reviewer im 12 giờ thì được merge nếu test xanh |

**Giả định tạm (chờ giảng viên):** S2 gọi một lần mỗi bài rồi gắn vào mọi phiên bản; tỷ lệ thành công tham khảo báo cả (a) % bài có dữ liệu S2, (b) % tham khảo đủ tiêu đề/tác giả/năm, (c) % tham khảo có arXiv ID; chờ 3 s tính từ lúc kết thúc yêu cầu trước; bản ghi rỗng = thư mục không có TeX + `meta.json` có trạng thái.

---

## 2. Tiến độ theo task

### Phase 0 — hạn tối 17/10

| Mã | Việc | Owner | TT | Ghi chú |
| --- | --- | --- | --- | --- |
| T0.1 | Hợp đồng dữ liệu | A + cả nhóm | 🟡 | Bản nháp ở `src/crawler/models.py`; chờ 5 người duyệt |
| T0.2 | Spike arXiv API | B | ⬜ | |
| T0.3 | Spike `/src/` | C | ⬜ | |
| T0.4 | Spike S2 + xin API key | D | ⬜ | |
| T0.5 | Hỏi giảng viên các câu còn mở | E | ⬜ | |
| T0.6 | Repo, lint, pre-commit, quy ước PR | A | 🟡 | Khung repo mã xong trong máy. Còn: commit/push, mời thành viên, bảo vệ `main`, CI xanh trên GitHub. Repo sách chưa tạo |
| T0.7 | File 20 dải + tổng số ID | A gom | ⬜ | Có mẫu `src/id_ranges.example.csv`; chưa ai gửi dải |

### Phase 1 — hạn CN 18/10

| Mã | Việc | Owner | TT |
| --- | --- | --- | --- |
| T1.1 | `http.py` (RateLimiter, retry) | A | ⬜ stub |
| T1.2 | `manifest.py` + `config.py` + ghi nguyên tử | A | 🟡 `config.py` xong, `manifest.py` stub |
| T1.3 | `ids.py` | B | ⬜ stub |
| T1.4 | Khung Jupyter Book + Pages | E | ⬜ |
| T1.5 | Fixture tổng hợp (`tests/conftest.py`) | C | ⬜ |

### Phase 2–5

| Phase | Task | TT |
| --- | --- | --- |
| 2 (hạn T2 19/10) | T2.1 `discovery` · T2.2 `source` · T2.3 `s2`. T2.4 đo mỏng trong pipeline (hạn 20/10). T2.5 sách viết trong lúc crawl | ⬜ |
| 3 (hạn trưa 21/10) | T3.1 `pipeline` · T3.2 `export`/`corpus`/`validate` · T3.3 pilot 30–50 ID · review trong 12 giờ | ⬜ |
| 4 (tối 21/10–25/10) | T4.1 crawl đủ · T4.2 chạy lại `failed` · T4.3 validate · T4.4 `stats/` | ⬜ |
| 5 (25–26/10) | T5.1 sách · T5.2 README · T5.3 đóng băng · T5.4 nộp Moodle chiều 26/10 | ⬜ (README có bản khởi tạo) |

---

## 3. Rủi ro và vấn đề đang mở

| # | Mô tả | Mức | Hành động | Owner |
| --- | --- | --- | --- | --- |
| R1 | Chưa đủ 20 dải; nếu ~100.000 bài thì một máy arXiv ~7 ngày, năm máy ~1,5 ngày. Lịch 10 ngày chỉ kịp nếu crawl bật tối 21/10 | Cao | Cộng tổng tối 17/10 (T0.7); pilot sáng 21/10 rồi bấm crawl cùng ngày | A |
| R2 | Chưa gán tên vai A–E; đường găng đi qua A | TB | Gán ở kickoff | Leader |
| R3 | Chưa có API key S2 | TB | D xin trong T0.4; key chỉ ở biến môi trường / Colab Secrets | D |
| R4 | CI lần push `Requirements/` đỏ: ruff format sửa code mẫu trong file markdown đề | Thấp | Loại `Requirements/` và `Plans/` khỏi ruff; push lại và xem Actions | Leader |
| R5 | Phiên bản thư viện chưa ghim (đang dùng khoảng phiên bản) | Thấp | Ghim sau pilot Colab | A |
| R6 | Đề yêu cầu repo mã **chỉ** có mã + `requirements.txt` + `README.md`; `STATUS.md` và `AGENTS.md` là file ngoài danh sách | TB | Ở T5.3 (đóng băng) quyết định: xóa hai file này khỏi repo nộp hoặc hỏi giảng viên | Leader |

---

## 4. Nhật ký thay đổi

Dòng mới nhất ở trên cùng.

| Ngày giờ | Thay đổi |
| --- | --- |
| 17/10 15:30 | CI đỏ vì ruff format đụng code mẫu trong `Requirements/milestone1-data-crawling-vi.md`. Loại `Requirements/` và `Plans/` khỏi ruff trong `pyproject.toml` |
| 17/10 14:40 | Nén lịch Milestone 1 còn 10 ngày trong playbook §2 và kế hoạch kỹ thuật: nộp mục tiêu chiều 26/10, crawl tối 21/10, hạn 5/11 là đệm. Làm mỏng spike, pilot, metrics, sách |
| 17/10 14:00 | Chuyển file trạng thái vào repo thành `STATUS.md` (nguồn duy nhất), thêm phần "Đọc nhanh"; thêm `AGENTS.md` hướng dẫn agent đọc/cập nhật file này. Thêm rủi ro R6 |
| 17/10 13:55 | Tạo file trạng thái (bản đầu, ở thư mục `Plans/`). Xác nhận repo mã còn ở `Initial commit`; `pytest` 17 pass / 26 skip |
| 17/10 ~13:45 | Dựng khung repo mã: 11 module stub kèm owner/task/điều kiện xong, `models.py` nháp hợp đồng, `config.py`, CLI, `corpus.py`, `validate_corpus.py`, `colab_run.ipynb`, test khung, `pyproject.toml`, pre-commit, CI, mẫu PR, README. Ghim `ruff==0.17.1` |
