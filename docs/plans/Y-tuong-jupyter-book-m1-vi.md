# Ý tưởng Jupyter Book — Milestone 1

> Tài liệu nội bộ: đối chiếu `project requirements/milestone1-data-crawling-vi.md`, `Plans/milestone1-implementation-plan-vi.md` (§4.2, §8, §10), `STATUS.md` (mục 0) và repo sách [GiaaPhuc/arxiv-book](https://github.com/GiaaPhuc/arxiv-book) / thư mục làm việc `arxiv-pipeline-book/`.
>
> **Mục đích:** thống nhất *nội dung*, *cấu trúc*, *nguồn số liệu* và *ai viết gì* trước Phase 2 (crawl 25.000 ID, hoàn thiện sách, nộp 6/11).

---

## 1. Sản phẩm nộp và ràng buộc

| Yêu cầu đề | Cách đáp ứng trong sách |
| --- | --- |
| Repo sách **riêng**, GitHub Pages, tiếng Việt | `arxiv-book` + workflow Actions; M1 là phần đầu, M2 thêm `m2/` sau |
| Mô tả **cách thu thập**, công cụ/thư viện | Chương triển khai + overview (đã có khung) |
| Thống kê: bài thành công, tỷ lệ, kích thước trước/sau bỏ ảnh, refs/bài, tỷ lệ metadata refs | Notebook thống kê + `summary.json` |
| Hiệu năng: wall time, RAM, đĩa; **Colab chỉ CPU** | Notebook hiệu năng + `performance.json` |
| Hình/bảng **sinh khi build** | Chỉ `.ipynb` / code trong MD; **không** PNG commit tay từ màn hình |
| Không nộp corpus | Sách đọc `stats/` nhỏ (copy từ `data/stats/`), không mount `data/papers/` |

**Tiêu chí chấm (5 mục)** — sách phục vụ trực tiếp mục *Báo cáo*; các mục còn lại được *minh chứng* qua số liệu và mô tả luồng:

- Báo cáo → đủ §8 + build sạch CI.
- Khám phá & thu thập → mô tả đúng D1–D4 (mọi phiên bản, `export.arxiv.org`, trích `.tex`/`.bib`, S2).
- Dữ liệu thu được → thống kê trạng thái + (tùy chọn) bảng mẫu từ parquet đã xuất.
- Hiệu năng → số Colab + giải thích resume / hai worker.

---

## 2. Hiện trạng so với kế hoạch §4.2

| File kế hoạch §4.2 | Trong `arxiv-pipeline-book` | Ghi chú |
| --- | --- | --- |
| `m1/01_overview.md` | ✅ Có | Số vẫn *chưa crawl*; cần cập nhật S2 batch sau pilot |
| `m1/02_implementation.md` | ⬜ Chưa | **Ưu tiên viết nháp trước crawl** (E + A/B/C đoạn module) |
| `m1/03_data_statistics.ipynb` | 🟡 Có, số mẫu | D: notebook nguồn; E: nhúng + `_toc` |
| `m1/04_performance.ipynb` | ⬜ Chưa | Dùng `logs/metrics.json` → `stats/performance.json` |
| `m1/05_issues_lessons.md` | ⬜ Chưa | Hạn chế, 429 S2, PDF trong dải, Colab ngắt |
| `stats/` | 🟡 Khi có crawl | `export_data.py` → copy sang repo sách |
| `_toc.yml` | 🟡 Chỉ 2 mục | Mở rộng theo bảng dưới |

**Gap quan trọng:** overview chưa nói rõ **một máy / 25.000 ID (D8)**, **batch S2 (lô 100)**, và **ba tỷ lệ tham khảo (a)(b)(c)** — bổ sung khi viết chương 2 và 3.

---

## 3. Đề xuất mục lục (`_toc.yml`)

```yaml
format: jb-book
root: m1/01_overview
chapters:
  - file: m1/02_implementation
  - file: m1/03_data_statistics
  - file: m1/04_performance
  - file: m1/05_issues_lessons
```

**Ý tưởng landing:** thêm một dòng ngắn ở đầu overview — link repo mã, link Colab (nếu có), ngày crawl, phiên bản `schema_version` — để giảng viên mở sách là thấy phạm vi ngay.

---

## 4. Nội dung từng chương (ý tưởng chi tiết)

### 4.1 `01_overview.md` (E chủ trì, cả nhóm review)

**Giữ / tinh chỉnh:**

- Mục tiêu M1, nguồn arXiv + S2, không OAI/Kaggle harvest.
- Bảng `papers` vs `references` (đã có — rất quan trọng cho chấm).
- `validate_corpus` — một đoạn “chúng tôi kiểm tra gì sau crawl”.

**Bổ sung:**

- Sơ đồ luồng end-to-end (Mermaid hoặc hình vẽ trong MD — *không* cần số liệu):

  ```mermaid
  flowchart LR
    ids[id_ranges.csv] --> disc[discovery API]
    disc --> src[source /src]
    disc --> s2[S2 batch + fallback]
    src --> data[data/papers]
    s2 --> data
    data --> val[validate_corpus]
    data --> exp[export_tables + export_stats]
    exp --> stats[stats/]
    stats --> book[Jupyter Book build]
  ```

- Bảng **5 dải / 25.000 ID** (tháng, khoảng ID) — copy từ `src/id_ranges.csv`, không bịa.
- Cam kết **lịch sự mạng:** ≥ 3 s arXiv, S2 có key + limiter thích ứng.

**Không làm:** nhét biểu đồ thống kê vào MD tĩnh — để notebook.

---

### 4.2 `02_implementation.md` (E ghép; A/B/C viết block module)

Cấu trúc đề xuất (tiếng Việt, có link tài liệu arXiv/S2):

1. **Phạm vi và ID** — `ids.py`, CSV bốn cột, `not_found` vs `failed`.
2. **Khám phá phiên bản** — lô 50, `idvK` cho ngày sửa trung gian, hậu tố vN mới nhất.
3. **Tải và trích nguồn** — HTTP một host, nhận diện PDF/tar/zip, trích an toàn, `no_source`, giữ `\includegraphics`.
4. **Semantic Scholar** — một lần/bài trên ID gốc; batch `POST /paper/batch`; fallback `/references`; venue kèm batch; 429/403.
5. **Pipeline & resume** — hai worker, SQLite manifest, ghi nguyên tử `.part/`.
6. **Xuất & kiểm tra** — `export_tables`, `export_stats`, `validate_corpus`.
7. **Công cụ** — bảng: Python 3.11, `requests`, `arxiv` 4.x, `pandas`/`pyarrow`, `ruff`, `pytest`; *không* bắt buộc liệt kê OAI/Kaggle ngoài một câu “hộp công cụ đề”.

**Ý tưởng minh họa khi build (tùy chọn):**

- Một code cell *ẩn output* chỉ in schema cột `papers`/`references` từ `summary.json` — hoặc pure markdown table từ đề.

**Tránh:** copy nguyên code dài từ repo — trích đoạn ≤ 15 dòng + link file trên GitHub.

---

### 4.3 `03_data_statistics.ipynb` (D viết logic; E tích hợp sách)

**Nguồn dữ liệu (copy vào `stats/` repo sách sau T4.4):**

| File | Dùng để |
| --- | --- |
| `summary.json` | Số tổng hợp + `definitions` (trích nguyên văn vào báo cáo) |
| `papers_stats.csv` | Trạng thái theo bài, S2, số phiên bản |
| `versions_stats.csv` | Phân phối kích thước, status từng vN |
| `papers.parquet` / `references.parquet` | (Tùy chọn) histogram nâng cao; file có thể lớn — ưu tiên CSV + summary |

**Bảng & hình nên có (sinh lúc execute notebook):**

| Chỉ số đề / §8 | Ý tưởng trình bày |
| --- | --- |
| Số bài thành công | Một số lớn + định nghĩa từ `summary["definitions"]` |
| Tỷ lệ thành công | Bar/stack: `ok`, `ok_no_tex`, `no_source`, `not_found`, `failed`, `corrupt` (phiên bản và/hoặc bài) |
| Kích thước TB trước/sau ảnh | Boxplot hoặc bar: `raw_archive_bytes`, `raw_uncompressed_bytes`, `kept_bytes` (nêu rõ đơn vị byte, median vs mean) |
| Số refs TB / bài | Histogram `reference_count`; có thể log-scale |
| Tỷ lệ metadata refs **(a)(b)(c)** | Ba metric riêng: (a) % bài có S2 ok; (b) TB theo bài của % ref đủ title+author+date; (c) % ref có arXiv ID |
| Phân phối số phiên bản | Bar `versions_per_paper` |
| Tỷ lệ phiên bản không TeX | % `no_source` / `ok_no_tex` |
| Nguyên nhân lỗi | Top-N từ `errors` trong summary (nếu có) |

**Đoạn markdown trong notebook:** “Số liệu lấy từ crawl ngày …, 25.000 ID, một máy” — thay placeholder “số mẫu” sau Phase 2.

**Phụ lục tùy chọn:** bảng theo **từng dải** (5 cột) nếu nhóm muốn so sánh nội bộ — lọc `papers_stats.csv` theo ID range; không bắt buộc đề.

---

### 4.4 `04_performance.ipynb` (D + A; số từ Colab CPU)

**Nguồn:** `stats/performance.json` (bản sao `data/logs/metrics.json`).

**Nội dung ý tưởng:**

| Hạng mục đề | Ý tưởng |
| --- | --- |
| Wall time end-to-end | Tổng giờ crawl 25k; ghi rõ có/không resume |
| Thời gian theo giai đoạn | Stacked bar: discovery / download / extract / s2 (từ keys `metrics`) |
| TB thời gian / bài | Mean wall hoặc tổng / số bài thành công |
| RAM đỉnh / TB | Series từ sampler + một dòng đối chiếu `time -v` hoặc Colab `!free` snapshot |
| Đĩa đỉnh / corpus cuối | `du` mẫu + kích thước `data/` sau crawl |
| Môi trường | Colab CPU, RAM/đĩa instance, ngày chạy, commit hash repo mã |

**Colab runner (T5.2):** notebook hoặc section “Cách tái lập đo hiệu năng” — chạy subset `--limit N` trên Colab **chỉ CPU**, không dùng GPU; API key qua Secrets.

**So sánh nhóm khác (tiêu chí hiệu năng):** một đoạn ngắn *định tính* — batch S2, một kết nối arXiv, resume — không cần tên nhóm khác.

---

### 4.5 `05_issues_lessons.md` (E + cả nhóm, sau crawl)

Gợi ý mục:

- **Hạn chế S2:** indexing trễ, ref không có arXiv ID, 429 — và cách nhóm xử lý (batch, backoff, `--s2-only` staging nếu đã pilot).
- **Hạn chế arXiv:** PDF-only trong dải (`2408.01045`), ID không tồn tại, tar lớn.
- **Vận hành D8:** một máy, sao lưu Drive, Colab ngắt — manifest resume.
- **Kiểm chứng:** ≥ 20 phiên bản so tay với trang arXiv (T4.3) — bảng 3–5 dòng ví dụ (ID, vN, khớp/không).
- **M2/M3:** corpus giữ nguyên bố cục; `references.json` đầy đủ vs bảng xuất.

Tone: trung thực, có số — phù hợp academic integrity.

---

## 5. Luồng làm việc số liệu → sách

```text
python src/run_crawl.py ...          # → data/
python src/export_data.py            # → data/stats/*
python src/validate_corpus.py        # PASS trước khi publish số
cp data/stats/{summary.json,*.csv,performance.json} → arxiv-pipeline-book/stats/
jupyter-book build .                 # CI GitHub Actions
```

**Quy ước:** số in trong `02_implementation` và overview **phải khớp** `summary.json` sau đóng băng; nếu resume nhiều lần, ghi footnote trong `04_performance`.

**Repo sách `requirements.txt`:** `jupyter-book`, `matplotlib`, `pandas`, `pyarrow` (nếu đọc parquet), pin tương thích CI.

---

## 6. Phân công và mốc (gắn task)

| Việc | Owner | Mốc | Done when |
| --- | --- | --- | --- |
| Khung `_toc` + chương 2 nháp | E | trước crawl | Build CI xanh, không broken links |
| Notebook 03 số mẫu → số thật | D → E | 1/11 (T4.4) | Mọi chỉ số §8 có trong notebook |
| Notebook 04 + đo Colab | D + A | 1–3/11 | Có `performance.json` từ run Colab CPU |
| Copy `stats/` + build sạch | E | 3/11 (T5.1) | Pages xanh; không ảnh tĩnh |
| Đoạn module trong ch. 2 | A/B/C | trong lúc crawl | Mỗi người ≥ 1 subsection |
| Kiểm chứng mẫu 20 vN | E | 1/11 (T4.3) | Đoạn trong ch. 5 |

---

## 7. Ý tưởng “điểm cộng” (không bắt buộc đề)

- **Interactive nhẹ:** `plotly` một histogram refs (nếu dependency OK trên Pages).
- **Bảng so sánh** “Công cụ đề gợi ý vs nhóm chọn” (API thuần + pipeline tự viết vs `arxiv.py` chỉ discovery).
- **Timeline crawl** (ngày, số ID/ngày) từ manifest — line chart tiến độ.
- **Liên kết chéo** sang `Plans/du-an-va-luong-xu-ly-hien-tai-vi.md` *không* đưa vào repo nộp — chỉ nội bộ; sách chỉ mô tả lại bằng lời.

Giữ phạm vi: đề không yêu cầu thì ưu tiên **đủ §8 + build sạch** trước polish.

---

## 8. Checklist trước nộp Moodle (sách)

- [ ] `_toc.yml` đủ 5 file m1 (hoặc 4 chương + root overview).
- [ ] Mọi hình từ notebook execute; xóa output hardcode cũ nếu CI execute lại.
- [ ] `stats/summary.json` cùng commit với lần build cuối.
- [ ] Chương 2 mô tả đúng batch S2 và một máy 25k (D8).
- [ ] Ba tỷ lệ tham khảo (a)(b)(c) có nhãn rõ trong ch. 3.
- [ ] Chương 4 ghi Colab CPU + ngày + giới hạn resume.
- [ ] README repo sách: cách build local (`jupyter-book build .`).
- [ ] Link Pages công khai dán Moodle cùng repo mã.

---

## 9. Tham chiếu nhanh

- Đề M1 báo cáo: `project requirements/milestone1-data-crawling-vi.md` — mục *Yêu cầu báo cáo (Jupyter Book)*.
- Danh sách số liệu đầy đủ: `Plans/milestone1-implementation-plan-vi.md` §8.
- Cấu trúc file sách mẫu: cùng file §4.2.
- Hiện trạng code & pilot: `STATUS.md` mục 0.
