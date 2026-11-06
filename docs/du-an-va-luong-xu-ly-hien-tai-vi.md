# arxiv-pipeline — Mô tả dự án và luồng xử lý hiện tại

> **Mục đích tài liệu:** Mô tả kiến trúc và luồng end-to-end **đúng 100% với mã nguồn** sau các đợt tối ưu **O1–O6**, **S1–S7** và kiến trúc **v3-fast Producer–Consumer** (cập nhật **06/11/2026**). Nền tảng phân tích hiệu năng và lộ trình mở rộng xem tại `docs/plans/Plan-toi-uu-luong-crawl-5000-id-vi.md`.
>
> **Trạng thái code:** **232 unit tests pass**, `ruff` sạch; Pilot 30 ID hoàn tất ✅ (`57` cặp phiên bản: `51` `ok`, `6` `no_source`, `0` `failed`); đang chạy **Milestone 1 Phase 2** (crawl 25.000 ID trên một máy) và đã khởi động **Milestone 2 Phase 0** (khóa `src/parsing/models.py` + khung pipeline đa nhân). Chi tiết đối chiếu đề bài: `STATUS.md` mục 0.

---

## 1. Bối cảnh dự án

| Khía cạnh | Nội dung |
| --- | --- |
| Môn / dự án | Nhập môn Khoa học Dữ liệu — pipeline arXiv (M1 thu thập dữ liệu → M2 parse LaTeX & khử trùng lặp → M3 matching BibTeX & huấn luyện mô hình) |
| Phạm vi tài liệu này | Trọng tâm **Milestone 1** (thu `.tex`/`.bib` mọi phiên bản, metadata arXiv, toàn bộ tham khảo S2, xuất thống kê) và điểm nối sang **Milestone 2** (`src/parsing/`) |
| Quy mô dữ liệu | `src/id_ranges.csv`: **5 dải × 5.000 = 25.000** ID gốc (tháng 06–08/2024), tính cả hai đầu (`inclusive`) |
| Vận hành | **Một máy** chạy `run_crawl.py` (quyết định D8); SQLite manifest hỗ trợ **resume** an toàn; **tuyệt đối không** chạy hai tiến trình crawler trên cùng thư mục `data/` |
| Repo liên quan | Mã nguồn: repo này. Báo cáo: Jupyter Book [arxiv-book](https://github.com/GiaaPhuc/arxiv-book) (repo riêng, triển khai GitHub Pages) |

**Tiến hóa hiệu năng crawl (ước lượng cho 5.000 ID $\approx 8.187$ phiên bản):**
- **v0 (Baseline):** ~15,0 giờ (`End-to-Start 3,5 s`, tuần tự 1 luồng, giải nén 2-pass, backup đồng bộ).
- **v1 (O1–O6 cơ bản):** ~10,8 giờ (`Start-to-Start 3,0 s`, tách `extract-worker`, 1-pass extract, nhưng bị nghẽn bởi ghi/đọc đĩa tạm ~40,6 GB).
- **v2 (S1–S5):** ~7,0 giờ (đệm RAM ban đầu, tối ưu S2 batch + checkpoint).
- **v3-fast (Mã hiện tại):** ~**6,4 – 7,0 giờ** wall-clock với chuẩn lịch sự **Start-to-Start ≥ 3,0 s**, kết hợp **Producer–Consumer song song hoàn toàn** (tải vào `bytearray` RAM $\le 96\text{ MiB}$, nhả socket ngay lập tức, giải nén 1-pass ở nền). Hướng mở rộng **Sliding-Window Credit Pacer** / mirror GCS (mục tiêu 4,0–4,5 giờ) được lưu ở mục 9 & 11.

---

## 2. Nguồn dữ liệu và ràng buộc lịch sự (Politeness Policy)

| Host | Dùng cho | Host thực tế | Giới hạn trong code | Hành vi đặc biệt khi gặp lỗi / Rate-limit |
| --- | --- | --- | --- | --- |
| **arXiv** | Atom API (`/api/query?id_list=...`) + tải gói nguồn (`/src/{id}vN`) | **`export.arxiv.org`** (1 `Session`, 1 `RateLimiter`, `HTTPAdapter(pool_connections=1, pool_maxsize=1)`, header `/src/` đặt `Accept-Encoding: identity`) | $\ge \mathbf{3,0\text{ s}}$ **Start-to-Start** giữa hai request (`CRAWLER_ARXIV_MIN_INTERVAL`, sàn cứng 3,0 s) | • **HTTP 403:** dừng toàn bộ lần chạy (`StopCrawl`, exit code `3`).<br/>• **HTTP 429:** chờ `Retry-After` (hoặc backoff), gọi `limiter.note_429()` để giãn nhịp (+1,0 s, tối đa 6,0 s), thử lại cùng request tối đa **4 lần** trước khi dừng.<br/>• **HTTP 2xx:** sau mỗi chuỗi 40 request thành công (`note_success()`), nhịp tự thu hẹp 0,5 s về sàn 3,0 s.<br/>• **5xx / lỗi mạng tạm thời:** retry tối đa `max_retries=5` với exponential backoff. |
| **Semantic Scholar (S2)** | Metadata bổ sung (`venue`) + danh sách trích dẫn (`references`) | `api.semanticscholar.org` | Nền $\mathbf{1,1\text{ s}}$ **End-to-Start** + jitter `0..0,05 s` (`AdaptiveRateLimiter`, chuẩn key 1 req/s) | • Đính kèm header `x-api-key` nếu có `S2_API_KEY` (không bao giờ in key ra log).<br/>• **HTTP 429:** chờ `Retry-After` (kẹp trong `30–600 s`), `note_429()` nhân đôi khoảng nghỉ (tối đa 5,0 s), **re-queue cả lô** thử lại tối đa 4 lần. Nếu tổng thời gian chờ 429 liên tiếp không có request thành công $\ge \mathbf{30\text{ phút}}$ $\rightarrow$ tắt `s2-worker` (`s2_disabled=True`).<br/>• **HTTP 403:** tắt `s2-worker` ngay lập tức (bảo toàn arXiv crawl). |

**Quy tắc bất khả xâm phạm (`AGENTS.md` & `CONTRIBUTING.md`):**
- Không crawl từ `arxiv.org` (chỉ dùng `export.arxiv.org`).
- Không mở nhiều kết nối song song tới arXiv (`_StreamResponse` giữ khóa `RateLimiter` cho tới khi đóng luồng đọc).
- Không commit `data/`, `.env`, API keys, `*.gz`, `*.parquet`, `*.sqlite`.

---

## 3. Kiến trúc module và đa luồng

### 3.1. Cây thư mục mã nguồn

```text
src/
├── id_ranges.csv             # 5 dải ID được giao (5 x 5.000 = 25.000 ID)
├── run_crawl.py              # CLI Milestone 1: mở rộng dải ID -> pipeline.run()
├── validate_corpus.py        # Kiểm tra toàn vẹn corpus (manifest vs đĩa, sha256, JSON...)
├── export_data.py            # Xuất bảng papers/references (.parquet/.csv) + summary.json
├── corpus.py                 # Tiện ích đọc/duyệt corpus trên đĩa
├── colab_run.ipynb           # Notebook vận hành crawl + auto-backup trên Google Colab
├── crawler/                  # Package thu thập dữ liệu (Milestone 1)
│   ├── models.py             # Hợp đồng dữ liệu M1 (SCHEMA_VERSION=1, không tự ý sửa)
│   ├── config.py             # Cấu hình từ biến môi trường / .env + CLI overrides
│   ├── http.py               # HostClient, _StreamResponse, RateLimiter, AdaptiveRateLimiter
│   ├── ids.py                # Chuẩn hóa ID, tách version, expand_range, id_to_dirname
│   ├── discovery.py          # Atom API lô 150 ID, tận dụng <updated>, xác nhận sub-batch 20
│   ├── source.py             # download_source (RAM <=96 MiB), extract_tex_bib (1-pass tar/gz)
│   ├── s2.py                 # POST /paper/batch (100 ID) + fallback GET /references (limit 1000)
│   ├── manifest.py           # SQLite manifest + atomic_write
│   ├── pipeline.py           # Điều phối Producer-Consumer đa luồng + resume + xử lý lỗi
│   ├── checkpoint.py         # Sao lưu nền nguyên tử (.tar.gz / mirror) + shrink guard + restore
│   ├── metrics.py            # Đo thời gian từng stage, ResourceSampler O(1), flush_run/flush_live
│   └── export.py             # Tổng hợp bảng Hugging Face & thống kê báo cáo (có cross_version_dedup)
├── run_parse.py              # CLI Milestone 2 (Phase 0): khung chạy parser đa nhân
└── parsing/                  # Package xử lý LaTeX & BibTeX (Milestone 2 Phase 0)
    ├── models.py             # Hợp đồng dữ liệu M2 (khóa ở Phase 0)
    └── pipeline.py           # Khung điều phối parse song song & profiler
```

**Phân công phụ trách (Milestone 1):** A — `http`, `manifest`, `pipeline`, `metrics`, `checkpoint` · B — `ids`, `discovery`, `s2` · C — `source`, fixtures · D — `export`, `corpus` · E — `validate_corpus`, Jupyter Book.

### 3.2. Năm luồng thực thi trong một phiên `pipeline.run()`

| Luồng (Thread) | Vai trò chính | Cô lập tài nguyên |
| --- | --- | --- |
| **1. `MainThread` (arXiv Producer)** | Chia chunk ID; gọi `discover` & `fetch_revised_dates` (lô 150); tải `/src/{id}vK` theo chunk `256 KiB` vào `bytearray` RAM ($\le 96\text{ MiB}$); **đóng HTTP stream & nhả `RateLimiter` ngay lập tức**; đẩy `_ExtractJob` vào `extract_queue` (`maxsize=8`). | Chỉ gọi `export.arxiv.org`; không giải nén tar, không gọi S2. |
| **2. `extract-worker` (Consumer)** | Lấy gói từ `extract_queue`; giải nén **1-pass** gzip/tar chỉ giữ `.tex`/`.bib` vào `.vK.part/tex/`, tính `sha256`; ghi `meta.json`, hoán đổi nguyên tử sang `vK/`; ghi `manifest.sqlite` **sau cùng**; định kỳ mỗi **60 s** gọi `_flush_metrics`; đẩy `_S2Job` sang `s2_queue` khi xong phiên bản cuối của bài. | Thuần CPU + Disk I/O; chạy song song 100% với lượt tải mạng kế tiếp. |
| **3. `s2-worker`** | Gom tối đa **100 bài** từ `s2_queue` (chờ gom 0,35 s); gọi `POST /paper/batch`; gọi fallback phân trang `/references` nếu bị cắt; ghi `references.json` vào mọi `vK/`, cập nhật `venue` cho `paper.json`, và ghi `s2_references.raw.json.gz` làm marker hoàn tất. | Chỉ gọi `api.semanticscholar.org`. |
| **4. `checkpoint-worker`** | Được kích hoạt ở nền mỗi khi đi qua `CRAWLER_BACKUP_EVERY_IDS` ID mới (và một lần cuối khi kết thúc): chụp snapshot SQLite bằng `sqlite3.Connection.backup`, đóng gói `papers/`, `logs/`, `stats/` ra `.tar.gz` (hoặc thư mục đích) kèm kiểm tra chống tụt lùi dữ liệu (`shrink protection`). | Không chặn `MainThread` (trừ bước `extract_queue.join()` ngắn trước khi mở snapshot). |
| **5. `resource-sampler`** | Luồng daemon của `metrics.ResourceSampler`: đo RSS RAM (mỗi 5 s) và dung lượng đĩa (mỗi 300 s) ở nền, giúp `sampler.snapshot()` đọc cực trị `O(1)` không tốn chi phí quét tiến trình trên luồng chính. | Chỉ đọc thông số hệ thống ở nền. |

---

## 4. Luồng xử lý End-to-End (`run_crawl.py`)

```mermaid
flowchart TB
  subgraph CLI["Khởi tạo"]
    R["run_crawl.py: expand_range 25.000 ID, khử trùng base ID"]
  end
  R --> P["pipeline.run(): nạp base_metrics, mở manifest.sqlite, bật ResourceSampler"]

  subgraph Main["1. MainThread — arXiv Network Producer (Start-to-Start >= 3.0s)"]
    CH["Chia chunk: min(2 * discovery_batch_size = 300, backup_every_ids)"]
    CH --> PLAN["_plan_chunk: đọc paper.json sẵn có (heal revised_dates) hoặc gom lô mới"]
    PLAN --> DIS["discover (lô 150) + xác nhận missing (sub-batch 20) + fetch_revised_dates"]
    DIS --> PJ["Ghi nguyên tử paper.json / đánh dấu not_found"]
    PJ --> DL["download_source (/src/{id}vK, chunk 256 KiB)<br/>Ngắt sớm PDF/ZIP ở 512 B | Nạp bytearray RAM <= 96 MiB"]
    DL --> REL["Đóng response -> Nhả RateLimiter ngay cho request kế tiếp"]
    REL --> EQ[("extract_queue (maxsize = 8)")]
  end

  subgraph EX["2. extract-worker — CPU & Disk Consumer"]
    EQ --> EXT["extract_tex_bib (1-pass gzip/tar, lọc .tex/.bib, sha256, overwrite trùng tên)"]
    EXT --> INST["_install_version: ghi .vK.part/meta.json -> os.replace sang vK/"]
    INST --> MAN["manifest.record(meta) [GHI SAU CÙNG]"]
    MAN --> FL60["Mỗi 60s: _flush_metrics -> logs/metrics.json"]
    MAN --> LAST{"Phiên bản cuối &<br/>chưa có raw.gz?"}
  end

  LAST -->|Có| SQ[("s2_queue")]
  subgraph S2["3. s2-worker — Semantic Scholar Batch"]
    SQ --> COL["Gom tối đa 100 bài (cửa sổ 0.35s)"]
    COL --> BAT["POST /graph/v1/paper/batch"]
    BAT --> FB{"referenceCount ><br/>len(refs)?"}
    FB -->|Có| PAG["GET /paper/{id}/references (limit=1000)"]
    FB -->|Không| STORE["Ghi references.json (mọi vK) + venue + s2_references.raw.json.gz"]
    PAG --> STORE
  end

  subgraph CKPT["4. checkpoint-worker — Sao lưu nền"]
    JOIN["Cuối chunk (nếu đến kỳ backup): extract_queue.join() + _flush_metrics"]
    JOIN --> BK["backup_data_dir -> arxiv-data.tar.gz + .meta.json (shrink guard)"]
  end

  P --> Main
  DL -.->|Cuối mỗi chunk| JOIN
```

### 4.1. Khám phá siêu dữ liệu (Discovery — tối ưu O2)

1. **Chia chunk:** Danh sách ID được duyệt theo từng chunk kích thước `2 * discovery_batch_size = 300` ID (hoặc nhỏ hơn nếu `backup_every_ids > 0`).
2. **Kiểm tra trạng thái cục bộ (`_plan_chunk`):**
   - Nếu đã có `data/papers/<dirname>/paper.json`: đọc lại `PaperMeta`. Nếu bài có `num_versions >= 2` nhưng `revised_dates` chưa đủ `(num_versions - 1)` phần tử (do lần chạy trước bị gián đoạn), đưa vào danh sách **auto-heal** để gọi bổ sung `fetch_revised_dates`.
   - Nếu `manifest.sqlite` đã ghi `(arxiv_id, 1)` là `not_found`: bỏ qua ngay.
   - Các ID còn lại được gom vào danh sách cần khám phá.
3. **Gọi Atom API theo lô 150 (`discover`):**
   - Gửi `GET /api/query?id_list=...&max_results=150`.
   - Từ mỗi `<entry>`, tách hậu tố `vN` của `<id>` làm tổng số phiên bản `num_versions = N`; `<published>` là `submission_date` (ngày của `v1`); `<updated>` là ngày của phiên bản mới nhất `vN`.
   - **Tận dụng `<updated>` (O2):** Với bài có đúng `N = 2` phiên bản, `<updated>` chính là ngày của `v2` $\rightarrow$ điền luôn `revised_dates = [updated]`, tiết kiệm 100% truy vấn phụ cho các bài 2 phiên bản! Với bài `N >= 3`, giữ `<updated>` cho `vN` và chỉ cần hỏi thêm `v2 .. v{N-1}`.
4. **Xác nhận ID vắng mặt theo sub-batch 20:**
   - Các ID không xuất hiện trong kết quả lô 150 được kiểm tra lại theo từng cụm **20 ID** (`_CONFIRM_BATCH_SIZE = 20`; chỉ tách lẻ từng ID nếu feed trả về `/api/errors`). Nếu vẫn không tồn tại $\rightarrow$ ghi manifest `(id, 1) = not_found` (không tạo thư mục bài).
5. **Lấy ngày phiên bản trung gian (`fetch_revised_dates`):**
   - Gom các định danh `idv2 .. idv{N-1}` vào các lô 150 (kèm cơ chế re-check sub-batch 20 nếu thiếu). Nếu sau khi re-check vẫn khuyết ngày của phiên bản nào, giữ chuỗi rỗng `""` tại vị trí đó và tăng bộ đếm `revised_dates_incomplete` (vẫn tiếp tục tải gói nguồn).
6. **Lưu đĩa:** Ghi nguyên tử `data/papers/<dirname>/paper.json`.

### 4.2. Tải gói nguồn & Giải nén bất đồng bộ (Producer–Consumer v3-fast)

Với mỗi bài hợp lệ, duyệt `version = 1 .. paper.num_versions`:

**A. Trên `MainThread` (`_download_version` $\rightarrow$ `download_source`):**
1. Đăng ký `(arxiv_id, version)` vào manifest (trạng thái `pending` nếu chưa có). Nếu trạng thái hiện tại đã thuộc nhóm kết thúc (`is_final`: `ok`, `ok_no_tex`, `no_source`, `not_found`, `corrupt`) $\rightarrow$ **bỏ qua**.
2. Gọi `GET /src/{arxiv_id}v{version}` với `stream=True` và đọc theo khung **`256 KiB`** (`_CHUNK_SIZE = 256 * 1024`).
3. **Ngắt sớm ở 512 bytes đầu (`O5` / `S2`):**
   - Ngay khi đọc đủ 4–512 bytes đầu tiên, kiểm tra magic bytes. Nếu là **`%PDF`** hoặc **`ZIP`** (`PK\x03\x04`, `PK\x05\x06`, `PK\x07\x08`): ngắt luồng tải ngay lập tức (nếu phần còn lại của phản hồi $\le 64\text{ KiB}$ thì đọc nốt để tái sử dụng kết nối TCP Keep-Alive), giữ lại đúng 512 bytes stub và ghi nhận `raw_archive_bytes` từ `Content-Length`.
4. **Đệm RAM Zero-Copy (`spool_max_bytes = 96 MiB`):**
   - Mọi gói nguồn $\le 96\text{ MiB}$ (>99,5% tổng số bài trên arXiv) được nối trực tiếp vào `bytearray` trong RAM — **0 byte ghi xuống `data/.tmp/`**.
   - Chỉ những gói khổng lồ $> 96\text{ MiB}$ mới tự động chuyển sang file tạm trong `data/.tmp/`.
5. **Nhả kết nối tức thì:** Ngay khi tải xong byte cuối (hoặc ngắt ở 512 B), khối `with response:` đóng lại, giải phóng slot `RateLimiter` để nhịp `Start-to-Start 3,0 s` của gói kế tiếp không bị trễ bởi thời gian giải nén. `DownloadResult` được đẩy vào `extract_queue`.

**B. Trên `extract-worker` (`_consume_extract_job` $\rightarrow$ `extract_tex_bib`):**
1. **Nhận diện & giải nén 1-pass mở Gzip duy nhất 1 lần (`O4` + `v3-fast`):**
   - Kiểm tra 512 bytes đầu: nếu là `%PDF` hoặc `ZIP` $\rightarrow$ trả về `Status.NO_SOURCE`.
   - Nếu là Gzip (`\x1f\x8b`): mở `gzip.GzipFile` **một lần duy nhất**, đọc thử 512 bytes giải nén đầu tiên để kiểm tra header tar (`ustar` hoặc checksum octal hợp lệ) rồi ghép lại bằng `_PrefixedReader` để truyền thẳng vào `tarfile.open(fileobj=..., mode="r|")` hoặc bộ ghi file `.tex` đơn.
2. **Lọc `.tex`/`.bib` & An toàn giải nén:**
   - Chặn triệt để path traversal (`..`, đường dẫn tuyệt đối, tên thiết bị Windows `CON`/`NUL`..., symlink/hardlink/device) và giới hạn dung lượng giải nén `max_extract_bytes = 2 GiB` (chống zip-bomb).
   - Với member không phải `.tex`/`.bib` (ảnh `.png`, `.pdf`, `.eps`...): cộng `member.size` vào `raw_uncompressed_bytes` và **bỏ qua ngay ở mức stream** (không đọc xuất ra đĩa, không băm `sha256`).
   - Với member `.tex`/`.bib`: vừa ghi vào `.vK.part/tex/<relative_path>`, vừa băm `sha256`. Nếu trong cùng một gói tar có 2 member trùng đường dẫn (ví dụ `JSSC.bib`), member sau **ghi đè an toàn** lên member trước theo đúng chuẩn POSIX tar (`S3`).
   - Cuối luồng tar: đọc cạn các block đệm 0 còn lại trên cùng luồng gzip để kiểm tra trọn vẹn trailer CRC32/ISIZE mà không cần mở file lần hai.
   - Nếu gói hợp lệ nhưng không có file `.tex`/`.bib` nào $\rightarrow$ `Status.OK_NO_TEX`; nếu có $\ge 1$ file $\rightarrow$ `Status.OK`; nếu archive hỏng hoặc vi phạm an toàn $\rightarrow$ `Status.CORRUPT`.
3. **Cài đặt nguyên tử & Ghi Manifest sau cùng:**
   - Ghi `.vK.part/meta.json` (kèm `version_date`, `crawled_at`, `s2_scope="paper"`). Nếu thư mục `vK/` cũ đã có `references.json` thì chép sang `.vK.part/`.
   - Hoán đổi nguyên tử `.vK.part` $\rightarrow$ `vK/`.
   - **Ghi `manifest.sqlite` ở bước cuối cùng:** đảm bảo nếu máy tắt giữa chừng, lần chạy sau sẽ tự động dọn `.vK.part` (bằng `os.scandir` nhanh) và tải lại sạch sẽ.

> **Lưu ý về `download_and_extract_source` trong `source.py`:** Module `source.py` còn cung cấp hàm cut-through giải nén trực tiếp ngay trên socket mạng (`_CountingChunkStream`). Tuy nhiên, trên môi trường sản xuất (`pipeline.py`), nhóm chủ đích dùng mô hình **RAM `bytearray` + `extract-worker`** để không giữ socket HTTP mở trong lúc giải nén/ghi đĩa, giúp giữ nhịp tải mạng sát sàn 3,0 s nhất.

### 4.3. Semantic Scholar — Worker nền theo lô (`s2-worker`)

1. **Điều kiện vào hàng đợi (`s2_queue`):**
   - Bài đã hoàn tất xử lý mọi phiên bản `1..num_versions` (do `extract-worker` đẩy vào khi xử lý xong phiên bản cuối, hoặc do `MainThread` đẩy vào khi resume các bài đã `is_final` trên manifest) **và** chưa có file marker `data/papers/<dirname>/s2_references.raw.json.gz`.
2. **Gom lô (`_collect_s2_batch`):**
   - Lấy job đầu tiên (blocking), sau đó chờ tối đa **`0,35 s`** để gom thêm các bài vừa xong gần nhau, tối đa **`100` bài/lô** (`S2_BATCH_SIZE = 100`).
3. **Truy vấn Batch & Fallback phân trang:**
   - Gọi `POST /graph/v1/paper/batch` với danh sách `["ARXIV:<id>", ...]` và các trường `venue,referenceCount,references.paperId,references.externalIds,references.title,references.authors,references.publicationDate,references.year`.
   - Nếu phần tử trả về `null`: bài chưa được S2 lập chỉ mục $\rightarrow$ ghi nhận `s2_not_indexed`, lưu raw `{"status": 404}` và danh sách `references.json = []`.
   - Nếu `referenceCount > len(references)` (S2 cắt bớt danh sách trích dẫn lồng trong batch): tự động gọi **fallback** `GET /graph/v1/paper/ARXIV:<id>/references?limit=1000` (phân trang `offset` đến hết).
4. **Lưu trữ nguyên tử (`_store_s2`):**
   - Ghi cùng một danh sách `references.json` vào **mọi thư mục phiên bản `v1..vN`** của bài (`s2_scope: "paper"`).
   - Nếu S2 trả về `venue` và `paper.json` chưa có `venue` từ arXiv $\rightarrow$ cập nhật bổ sung vào `paper.json`.
   - **Ghi `s2_references.raw.json.gz` sau cùng** làm dấu hiệu hoàn tất S2 cho bài đó.

---

## 5. Cấu trúc thư mục dữ liệu (`data/`)

```text
data/
├── manifest.sqlite                     # Bảng version_pairs: (arxiv_id, version) -> status, timestamps, metrics, error
├── logs/
│   ├── crawl.log                       # Log chi tiết mọi request HTTP và tiến độ chunk
│   └── metrics.json                    # Thời gian tích lũy từng stage, đỉnh RAM/Disk, bộ đếm S2 (resume-safe)
├── .tmp/                               # Thư mục tạm (trống >99,5% thời gian nhờ đệm RAM 96 MiB)
├── papers/
│   └── <dirname>/                      # Tên thư mục từ ids.id_to_dirname() (VD: 2408.01045)
│       ├── paper.json                  # PaperMeta (title, authors, submission_date, revised_dates, venue...)
│       ├── s2_references.raw.json.gz   # Phản hồi S2 gốc nén gzip (marker đã xong S2)
│       ├── v1/
│       │   ├── meta.json               # VersionMeta (status, raw/kept bytes, danh sách files kèm sha256)
│       │   ├── references.json         # Danh sách Reference từ S2
│       │   └── tex/                    # Cây thư mục chỉ chứa các file .tex và .bib giữ nguyên cấu trúc
│       └── v2/
│           └── ...
└── stats/                              # Sinh ra bởi src/export_data.py
    ├── papers.parquet / papers.csv     # Bảng papers chuẩn bị cho Hugging Face (Milestone 3)
    ├── references.parquet / .csv       # Bảng references (chỉ giữ tham khảo có ref_arxiv_id)
    ├── summary.json                    # Toàn bộ thống kê báo cáo M1 + cross_version_dedup (sha256)
    └── performance.json                # Tóm tắt hiệu năng (wall time, active stage seconds, RAM/Disk peak)
```

---

## 6. Cơ chế Resume, Checkpoint nền & Live Telemetry

| Tình huống vận hành | Hành vi của hệ thống |
| --- | --- |
| **Ngắt giữa chừng (Ctrl+C / mất kết nối Colab)** | Khối `finally` của `pipeline.run()` đợi `extract-worker` và `s2-worker` kết thúc an toàn, dừng `ResourceSampler`, flush `metrics.json`, và chạy một bản backup cuối (nếu cấu hình). Khi chạy lại **cùng câu lệnh**: các cặp `(id, version)` đã `is_final` được bỏ qua ngay; cặp `failed`/`pending` được tải lại; bài thiếu `s2_references.raw.json.gz` được đưa lại vào `s2_queue`. |
| **Checkpoint tự động lên Google Drive (`O6`)** | Khi đặt `--backup-dir` (hoặc `CRAWLER_BACKUP_DIR`) và `--backup-every-ids N` (trên Colab thường đặt `N = 1000`):<br/>• Khi khởi động lại (resume), hàm `_count_leading_done_ids` đếm số ID đầu dải đã hoàn tất cả arXiv lẫn S2 để đặt mốc `checkpoint_last_seen`, tránh backup lại ngay lập tức.<br/>• Cứ mỗi `N` ID mới đi qua, luồng chính gọi `extract_queue.join()` + `_flush_metrics`, rồi bật `checkpoint-worker` nén streaming sang `arxiv-data.tar.gz` ở nền.<br/>• **Shrink Protection (`checkpoint.py`):** Kèm file cạnh `arxiv-data.tar.gz.meta.json` lưu `(final_pairs, total_pairs)`. Bản backup mới **không bao giờ ghi đè** lên bản backup cũ trên Drive nếu có số cặp hoàn tất ít hơn (bảo vệ tuyệt đối trước lỗi reset máy ảo Colab). |
| **Live Metrics Checkpointing (`V3-3`)** | Thay vì chỉ lưu `metrics.json` khi thoát chương trình, `pipeline.py` nạp `base_metrics` lúc bắt đầu và gọi `metrics.flush_run` (hoặc alias `flush_live`) **mỗi 60 giây trên `extract-worker`**, **cuối mỗi chunk**, **trước mỗi lần backup**, và **trong khối `finally`** mà không bị cộng trùng (`idempotent` trong cùng phiên). Nhờ đó `performance.json` luôn khớp 100% thực tế qua nhiều lần resume. |
| **Khử trùng lặp xuyên phiên bản (`V3-4`)** | Khi chạy `export_data.py`, module `export.py` đối chiếu mã băm `sha256` của từng file `.tex`/`.bib` giữa `v1..vN` để xuất trường `cross_version_dedup` trong `summary.json` (số phiên bản giữ nguyên 100% nội dung TeX/Bib, số file và số byte trùng lặp), nối trực tiếp sang bài toán Deduplication của Milestone 2. |

---

## 7. Công thức và Ước lượng Thời gian

Ký hiệu: $P$ là số bài cần crawl ($5.000$ ID/dải hoặc $25.000$ ID toàn cục); $\bar{V}$ là số phiên bản trung bình mỗi bài (pilot đo được $\bar{V} \approx 1,9$, thực tế dải lớn $\approx 1,64 - 1,90$); $R = \sum_{i=1}^{P} V_i$ là tổng số phiên bản.

1. **Số request tới `export.arxiv.org`:**
   $$N_{\text{arxiv}} = N_{\text{discovery\_batches}} + N_{\text{confirm\_subbatches}} + N_{\text{revised\_batches}} + R$$
   Với lô `150` ID và tối ưu `<updated>` (`O2`), phần discovery cho $5.000$ ID chỉ tốn $\approx 34 + 15 + 10 \approx 60$ requests ($\approx 3\text{ phút}$).

2. **Thời gian tầng arXiv (`Start-to-Start >= 3,0 s` + Producer–Consumer):**
   - Sàn lý thuyết của bộ điều nhịp: $T_{\text{floor}} = 3,0\text{ s} \times N_{\text{arxiv}}$.
   - Nhờ nhịp **Start-to-Start** và giải nén tách sang `extract-worker`, mọi gói nguồn có thời gian tải mạng $\le 3,0\text{ s}$ đều đạt chu kỳ đúng $3,0\text{ s/request}$; các gói PDF/ZIP được ngắt ngay ở byte 512 chỉ mất $\sim 0,1\text{ s}$ mạng.
   - **Cho 1 dải 5.000 ID ($R \approx 8.187$ phiên bản):** $8.187 \times 3,0\text{ s} \approx 6,8\text{ giờ}$ (cộng các gói lớn $>3\text{ s}$ $\rightarrow$ thực tế **~6,4 – 7,0 giờ**).
   - **Cho toàn bộ 25.000 ID ($R \approx 41.000 - 47.500$ phiên bản):** **~35 – 42 giờ** chạy tích lũy (khoảng 1,5 – 2 ngày với cơ chế resume/backup).

3. **Thời gian tầng Semantic Scholar (`s2-worker` chạy song song):**
   $$T_{\text{s2}} \approx 1,1\text{ s} \times \left(\lceil P / 100 \rceil + F_{\text{fallback}}\right)$$
   Do chạy trên luồng riêng song song hoàn toàn với arXiv, $T_{\text{s2}}$ bị che khuất (hidden) bên dưới thời gian chờ arXiv khi có `S2_API_KEY` ổn định.

---

## 8. Hợp đồng dữ liệu (Data Contracts)

- **Milestone 1 (`src/crawler/models.py` — `SCHEMA_VERSION = 1`):**
  - `PaperMeta`, `VersionMeta`, `FileEntry` (`path`, `size`, `sha256`), `Reference` (`ref_s2_id`, `ref_arxiv_id`, `ref_title`, `ref_authors`, `ref_submission_date`).
  - Hai bảng xuất cho Hugging Face (nộp ở Milestone 3):
    - `papers`: `arxiv_id, title, authors, submission_date, revised_dates, venue`.
    - `references`: chỉ lọc các tham khảo **có `ref_arxiv_id`**, gồm `arxiv_id, ref_arxiv_id, ref_title, ref_authors, ref_submission_date, ref_s2_id` (trên đĩa cục bộ vẫn giữ 100% tham khảo).
- **Milestone 2 (`src/parsing/models.py` — đã khóa ở Phase 0 ngày 06/11):**
  - Đọc trực tiếp từ cấu trúc `data/papers/<dirname>/vK/tex/` và `meta.json` của Milestone 1 để phân rã cây phân cấp LaTeX, chuẩn hóa công thức và trích xuất BibTeX.

---

## 9. Bảng đối chiếu Triển khai thực tế vs Kế hoạch tối ưu (`Plan-toi-uu-luong-crawl-5000-id-vi.md`)

| Mã hạng mục | Nội dung tối ưu | Trạng thái trong code | Chi tiết thực tế trong repo |
| --- | --- | --- | --- |
| **O1** | Nhịp `Start-to-Start 3,0 s` + 1 kết nối TCP Keep-Alive | ✅ Đã triển khai | `RateLimiter` trong `http.py`, `HTTPAdapter(pool_connections=1, pool_maxsize=1)` |
| **O2** | Discovery lô 150 + tận dụng `<updated>` + sub-batch 20 + auto-heal | ✅ Đã triển khai | `discovery.py` + `_plan_chunk` trong `pipeline.py` |
| **O3 + v3-fast** | Tách `extract-worker` + Đệm RAM `bytearray` $\le 96\text{ MiB}$ | ✅ Đã triển khai | `download_source` nhả socket ngay sau khi đọc vào RAM; `extract_tex_bib` chạy nền trên `extract-worker` (`maxsize=8`) |
| **O4 + S3** | Giải nén `.tar.gz` 1-pass (mở gzip 1 lần) + ghi đè entry trùng tên | ✅ Đã triển khai | `_write_tar_members_from_stream` bỏ qua giải nén ảnh, kiểm tra trailer CRC32, xử lý `JSSC.bib` |
| **O5 + S2** | Ngắt sớm stream `/src/` ở 512 bytes cho `%PDF` và `ZIP` | ✅ Đã triển khai | `_EARLY_ABORT_MAGICS` + drain $\le 64\text{ KiB}$ giữ ấm socket |
| **O6 + S7** | Backup nền `checkpoint-worker` + Shrink Guard + Streaming `.tar.gz` | ✅ Đã triển khai | `checkpoint.py` (`backup_data_dir`, `restore_data_dir`, `.meta.json`) |
| **S1–S3 (S2)** | S2 Batch 100 ID + Fallback `/references` + `AdaptiveRateLimiter` | ✅ Đã triển khai | `s2.py`, `_s2_worker` trong `pipeline.py` |
| **V3-1** | Cut-through giải nén trực tiếp trên socket (`download_and_extract_source`) | 🟡 Có hàm & test | Đã cài đặt trong `source.py`; `pipeline.py` dùng RAM `bytearray` + `extract-worker` để tránh giữ kết nối HTTP trong lúc giải nén |
| **V3-2** | `SlidingWindowRateLimiter` (Credit Pacer `~1,95 s`) cho `/src/` | ⬜ Chỉ ở Plan | Code giữ chuẩn lịch sự nghiêm ngặt `>= 3,0 s` trên một limiter chung để tuân thủ tuyệt đối quy định đề bài |
| **V3-3** | Live Metrics Checkpointing + `ResourceSampler.snapshot()` $O(1)$ | ✅ Đã triển khai | `metrics.flush_run` / `flush_live` gọi định kỳ 60 s, cuối chunk, trước backup và khi thoát |
| **V3-4** | Thống kê trùng lặp nội dung xuyên phiên bản (`cross_version_dedup`) | ✅ Đã triển khai | `export.py` tính trùng lặp `sha256` giữa `v1..vN` trong `summary.json` |
| **V3-GCS** | Hybrid Prefetch từ `gs://arxiv-dataset` | ⬜ Chỉ ở Plan | Ý tưởng mở rộng trong plan, không bật mặc định để tuân thủ quy tắc chỉ tải từ `export.arxiv.org` |
| **S4** | Cờ CLI `--s2-only` và bảng `s2_state.sqlite` riêng | ⬜ Chưa triển khai | Hiện resume S2 tự động thông qua việc kiểm tra thiếu `s2_references.raw.json.gz` |

---

## 10. Lệnh vận hành và Tham chiếu nhanh

```bash
# 1. Kiểm tra kế hoạch mở rộng ID (không gọi mạng, không ghi file)
python src/run_crawl.py --ranges src/id_ranges.csv --dry-run

# 2. Chạy thử nghiệm (Pilot 30 ID đầu)
python src/run_crawl.py --ranges src/id_ranges.csv --limit 30 --data-dir data

# 3. Chạy thu thập thực tế (resume tự động khi chạy lại cùng lệnh)
# Thiết lập biến môi trường (hoặc đặt trong file .env):
#   CRAWLER_CONTACT="email-nhom@student.hcmus.edu.vn"
#   S2_API_KEY="your-s2-api-key"
python src/run_crawl.py \
  --ranges src/id_ranges.csv \
  --data-dir data \
  --backup-dir /path/to/drive/arxiv-data.tar.gz \
  --backup-every-ids 1000

# 4. Kiểm tra toàn vẹn corpus và xuất thống kê cho Jupyter Book
python src/validate_corpus.py --data-dir data
python src/export_data.py --data-dir data

# 5. Kiểm tra chất lượng mã nguồn (bắt buộc trước khi commit/PR)
ruff check . && ruff format . && pytest
```

**Mã thoát (Exit codes) của `src/run_crawl.py`:**
- `0`: Hoàn tất toàn bộ dải ID.
- `2`: Lỗi cấu hình (thiếu `CRAWLER_CONTACT` hoặc file `--ranges` không hợp lệ).
- `3`: `StopCrawl` — arXiv trả về HTTP `403` hoặc quá số lần thử lại `429` (dừng ngay, báo nhóm).
- `130`: Người dùng ngắt bằng `Ctrl+C` (`KeyboardInterrupt` — dữ liệu và metrics đã được lưu an toàn).

---

## 11. Giới hạn đã biết & Lưu ý khi viết Báo cáo

1. **Phân biệt rõ trong báo cáo Jupyter Book:**
   - **Đã chạy thực tế trong code (`v3-fast`):** Nhịp `Start-to-Start >= 3,0 s` trên 1 kết nối, Discovery lô 150 + `<updated>`, Producer–Consumer đệm RAM `96 MiB` + `extract-worker` 1-pass, ngắt sớm PDF/ZIP ở 512 B, S2 Batch 100 ID, Checkpoint nền chống tụt lùi, Live Metrics $O(1)$ và thống kê `cross_version_dedup`.
   - **Đề xuất thảo luận / Hướng phát triển thêm (`Plan v3`):** Cơ chế *Sliding-Window Credit Pacer* và *Hybrid Cloud Mirror (`gs://arxiv-dataset`)*.
2. **Môi trường Windows:** Trình diệt virus (Windows Defender) đôi khi quét file trong `.vK.part` lúc `os.replace`; `pipeline.py` đã bọc bắt lỗi `OSError`, khôi phục bản cũ và đánh dấu `failed` để lần chạy sau tự retry (rủi ro R8).
3. **Đóng băng nộp bài (R6):** Trước khi nộp link repo mã Milestone 1, cần dọn các file quản trị nội bộ (`STATUS.md`, `AGENTS.md`, `CONTRIBUTING.md`, `docs/`, `project requirements/`) theo đúng quy định đề bài (chỉ giữ `.py`, `.ipynb`, `requirements.txt`, `README.md`).

---

*Tài liệu được rà soát và đồng bộ với mã nguồn `arxiv-pipeline` ngày **06/11/2026**.*
