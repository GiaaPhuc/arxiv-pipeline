# arxiv-pipeline — Mô tả dự án và luồng xử lý hiện tại

> **Mục đích tài liệu:** Mô tả kiến trúc và luồng end-to-end **đúng 100% với mã nguồn** sau các đợt tối ưu **O1–O6**, **S1–S7**, kiến trúc **v3 Producer–Consumer**, **v3.1 Resilience & QA Alignment (`U1–U6`)**, **v3.2 Sub-7h Speed Tuning (`T1–T5` + Fast-Defer Queue)**, **v3.3 Thermal-Aware Pacing**, **v3.4 Extract-Turbo (`E1–E5`)**, **v3.5 Anti-Cascade 429 & Smart Backup**, **v3.6 Persistent Partial Resume, Linear Short Cooldown & Live S2 Batching**, **v3.7 3.15s Sweet-Spot Pacing, Backup Storm Throttle & S2 Anti-Requeue Loop** và **v3.8 5.0s Fast-Recovery Pacing, 0..0.30s Jitter & 300-ID Proactive Cooldown** (cập nhật **08/11/2026**). Nền tảng phân tích hiệu năng và thiết kế chi tiết xem tại `docs/plans/Plan-toi-uu-luong-crawl-5000-id-vi.md` và `docs/plans/Plan-chong-qua-nhiet-ip-va-crawl-lien-tuc-vi.md`.
>
> **Trạng thái code:** **359 unit tests pass** (261 nền + 98 test C M2; B/D trong test tích hợp là giả), `ruff` sạch; C đã thử độc lập 10 bài / 26 phiên bản thật bằng hook truyền nguyên văn, còn chờ B/D thật. Pilot 30 ID hoàn tất ✅ (`57` cặp phiên bản: `51` `ok`, `6` `no_source`, `0` `failed`); đang chạy **Milestone 1 Phase 2** (crawl 25.000 ID trên một máy) và đã khởi động **Milestone 2 Phase 0** (khóa `src/parsing/models.py` + khung pipeline đa nhân). Chi tiết đối chiếu đề bài: `STATUS.md` mục 0.

---

## 1. Bối cảnh dự án

| Khía cạnh | Nội dung |
| --- | --- |
| Môn / dự án | Nhập môn Khoa học Dữ liệu — pipeline arXiv (M1 thu thập dữ liệu → M2 parse LaTeX & khử trùng lặp → M3 matching BibTeX & huấn luyện mô hình) |
| Phạm vi tài liệu này | Trọng tâm **Milestone 1** (thu `.tex`/`.bib`/`.bbl` mọi phiên bản, metadata arXiv, toàn bộ tham khảo S2, xuất thống kê) và điểm nối sang **Milestone 2** (`src/parsing/`) |
| Quy mô dữ liệu | `src/id_ranges.csv`: **5 dải × 5.000 = 25.000** ID gốc (tháng 06–08/2024), tính cả hai đầu (`inclusive`) |
| Vận hành | **Một máy** chạy `run_crawl.py` (quyết định D8); SQLite manifest hỗ trợ **resume** an toàn; khóa tiến trình cục bộ `_acquire_workspace_lock` (`msvcrt`/`fcntl` tại `tempfile.gettempdir()`) tự động chặn chạy trùng 2 tiến trình crawler trên cùng thư mục `data/` |
| Repo liên quan | Mã nguồn: repo này. Báo cáo: Jupyter Book [arxiv-book](https://github.com/GiaaPhuc/arxiv-book) (repo riêng, triển khai GitHub Pages) |

**Tiến hóa hiệu năng crawl (ước lượng cho 5.000 ID $\approx 8.187$ phiên bản):**
- **v0 (Baseline):** ~15,0 giờ (`End-to-Start 3,5 s`, tuần tự 1 luồng, giải nén 2-pass, backup đồng bộ).
- **v1 (O1–O6 cơ bản):** ~10,8 giờ (`Start-to-Start 3,0 s`, tách `extract-worker`, 1-pass extract, nhưng bị nghẽn bởi ghi/đọc đĩa tạm ~40,6 GB).
- **v2 (S1–S7):** ~7,0 – 7,4 giờ (đệm RAM 32 MiB, tối ưu S2 batch + streaming checkpoint, nhưng còn kẹt trần thích ứng 429 ở 6,0 s).
- **v3 + v3.1 (`U1–U6`) + v3.2 (`T1–T5`) + v3.3 (`Thermal-Aware Pacing`) + v3.4 (`Extract-Turbo E1–E5`) + v3.5 (`Anti-Cascade 429 & Smart Backup` — Mã hiện tại):**
  - **Giờ trống / ít 429:** **`~6,85 – 6,92 giờ` (< 7,0 giờ)** — đạt ~99% trần lý thuyết `6,85 giờ` của chuẩn lịch sự **Start-to-Start ≥ 3,0 s** trên 1 kết nối đơn (`+0,00 s` overhead cho `96%` gói nguồn $\le 2,50\text{ s}$; xoay vòng TCP socket mỗi `400` request ẩn hoàn toàn trong thời gian chờ `RateLimiter`; hồi phục nhịp `3,00 s` sau **3** bài thành công; không bao giờ ngủ phạt oan `60 s` sau các bài tải thành công; pool `extract-worker` 2 luồng + `_FastGzipStream` `512 KiB` triệt tiêu nghẽn giải nén trên các bài 50–200 MB nhiều hình ảnh).
  - **Giờ cao điểm / IP Colab bị bóp băng thông:** **`~6,92 – 7,15 giờ` (< 7,2 giờ)** — chặn đứng hiệu ứng tuyết lở `ChunkedEncodingError -> retry 1,5 s -> bão 429 dây chuyền` nhờ **Retry nối tiếp `HTTP 206 Range` tuân thủ nghiêm `End-to-Start >= 3,0 s` (`3,0 s → 4,5 s → 6,0 s`, tối đa 3 lần nối / 4 lượt thử kèm đổi socket sạch)**, **Cooldown leo thang thông minh ngay từ `429` đầu tiên (`60 s → 120 s → 240 s → 360 s → 600 s`, hạ bậc sau 10 lượt `2xx` sạch)** và **Backup nền song song có kiểm tra thay đổi (`skip_if_unchanged=True`, tốn `0 s` khi chưa có bài mới)**.
  - **Toàn bộ `25.000 ID` ($\approx 41.000$ phiên bản):** **`~34,3 – 35,8 giờ`** treo máy liên tục 24/7 tự động 100%, `0 failed`.

---

## 2. Nguồn dữ liệu và ràng buộc lịch sự (Politeness Policy)

| Host | Dùng cho | Host thực tế | Giới hạn trong code | Hành vi đặc biệt khi gặp lỗi / Rate-limit |
| --- | --- | --- | --- | --- |
| **arXiv** | Atom API (`/api/query?id_list=...`) + tải gói nguồn (`/src/{id}vN`) | **`export.arxiv.org`** (1 `Session`, 1 `RateLimiter`, `_SingleConnAdapter(pool_connections=1, pool_maxsize=1)` với `TCP_NODELAY = 1` & bảo toàn Linux `tcp_rmem` auto-tuning tới `6 MiB`, xoay vòng TCP `recycle_session()` mỗi `400` request hoặc ngay khi đứt stream với `0 ms` overhead, header `/src/` đặt `Accept-Encoding: identity`) | $\ge \mathbf{3,0\text{ s}}$ **Start-to-Start** giữa hai request (`CRAWLER_ARXIV_MIN_INTERVAL`, sàn cứng 3,0 s) + sàn nghỉ hậu tải `min_idle_gap = 0,50 s` sau các bài tải `> 2,50 s` (giữ `Duty Cycle <= 85%`, `+0,00 s` overhead cho các bài $\le 2,50\text{ s}$) | • **HTTP 403:** dừng toàn bộ lần chạy (`StopCrawl`, exit code `3`).<br/>• **HTTP 429 trên `/src/` — Defer tức thì + Escalating Cooldown (`U1` + `T1` + `v3.5`, bật mặc định trong `Config.from_env()`):** gọi `limiter.note_429()` nhích nhẹ **`+0,15 s`** (trần cứng **`3,30 s`**) kèm `schedule_retry_backoff(3,15 s)` tính từ `_last_done_at`. Không đứng chờ `15 s` retry tại chỗ và **bỏ hoàn toàn cơ chế `"0 s extra wait"`**: đẩy ngay ID đó vào `deferred_ids`, đóng socket cũ (`recycle_session()`) và ngủ **Cooldown leo thang `60 s → 120 s → 240 s → 360 s → 600 s`** theo `arxiv_429_streak` (tôn trọng `Retry-After` nếu lớn hơn, giữ nhịp thích ứng `3,15–3,30 s` sau khi dậy; chỉ hạ `1` bậc streak sau **10** lượt tải `/src/` `2xx` sạch liên tiếp).<br/>• **Không phạt oan bài tải thành công (`v3.5`):** phản hồi `2xx` chậm header không cộng điểm `thermal_score` (mỗi `2xx` giảm `-0,5`), tuyệt đối không kích hoạt ngủ `60 s` sau các phiên bản đã tải `ok`/`no_source`.<br/>• **HTTP 2xx (`T1` + `v3.3`):** sau mỗi chuỗi **3 request thành công** (`note_success()`), nhịp tự thu hẹp `-0,15 s` về sàn `3,0 s`.<br/>• **Đứt stream / lỗi mạng tạm thời (`T2` + `v3.5`):** đóng ngay socket hỏng (`recycle_session()`), chờ lịch sự **`End-to-Start = 3,0 s → 4,5 s → 6,0 s`** (`>= min_interval`, không bao giờ `1,5 s`) và nối tiếp `HTTP 206 Range` tối đa **3 lần nối (`_MAX_TOTAL_STREAM_ATTEMPTS = 4` lượt thử / phiên bản**, dừng sớm sau `2` nhịp `< 512 KiB`). Nếu hết 4 lượt ở pass chính, tự động defer bài sang `deferred_ids` (và ngủ cooldown nếu $\ge 2$ bài liên tiếp đứt stream). |
| **Semantic Scholar (S2)** | Metadata bổ sung (`venue`) + danh sách trích dẫn (`references`) | `api.semanticscholar.org` | Nền $\mathbf{1,1\text{ s}}$ **End-to-Start** + jitter `0..0,05 s` (`AdaptiveRateLimiter`, chuẩn key 1 req/s) | • Đính kèm header `x-api-key` nếu có `S2_API_KEY` (`SecretRedactingFilter` tự động che mọi khóa trong log — **U5**).<br/>• **HTTP 429 (T1):** chờ `Retry-After` (hoặc backoff bậc thang `15 s → 30 s → 60 s` theo `rate_attempt`, kẹp trong `15–600 s`), `note_429()` nhân đôi khoảng nghỉ (tối đa 5,0 s), thử lại lô tối đa 4 lần. Nếu tổng thời gian chờ 429 liên tiếp không có request thành công $\ge \mathbf{30\text{ phút}}$ $\rightarrow$ tắt `s2-worker` (`s2_disabled=True`, có thể vét bù sau bằng `--s2-only` — **U2**).<br/>• **HTTP 403:** tắt `s2-worker` ngay lập tức (bảo toàn arXiv crawl). |

**Quy tắc bất khả xâm phạm (`AGENTS.md` & `CONTRIBUTING.md`):**
- Không crawl từ `arxiv.org` (chỉ dùng `export.arxiv.org`).
- Không mở nhiều kết nối song song tới arXiv (`_StreamResponse` giữ khóa `RateLimiter` cho tới khi đóng luồng đọc; trạng thái cooldown được lưu liên tiến trình vào `data/logs/rate_state.json` — **U3**).
- Không commit `data/`, `.env`, API keys, `*.gz`, `*.parquet`, `*.sqlite`.

---

## 3. Kiến trúc module và đa luồng

### 3.1. Cây thư mục mã nguồn

```text
src/
├── id_ranges.csv             # 5 dải ID được giao (5 x 5.000 = 25.000 ID)
├── run_crawl.py              # CLI Milestone 1: mở rộng dải ID -> pipeline.run() (--auto-resume-rate-limit, --s2-only)
├── validate_corpus.py        # Kiểm tra toàn vẹn corpus (manifest vs đĩa, .tex/.bib/.bbl, sha256, JSON...)
├── export_data.py            # Xuất bảng papers/references (.parquet/.csv) + summary.json + performance.json
├── corpus.py                 # Tiện ích đọc/duyệt corpus trên đĩa
├── colab_run.ipynb           # Notebook vận hành crawl + auto-backup trên Google Colab
├── crawler/                  # Package thu thập dữ liệu (Milestone 1)
│   ├── models.py             # Hợp đồng dữ liệu M1 (SCHEMA_VERSION=1, không tự ý sửa)
│   ├── config.py             # Cấu hình từ biến môi trường / .env + CLI overrides (extract_workers=2, extract_max_queue_bytes=192 MiB)
│   ├── http.py               # HostClient, _SingleConnAdapter (TCP_NODELAY, Linux tcp_rmem), RateLimiter (+0.15s, trần 3.3s), rate_state.json
│   ├── ids.py                # Chuẩn hóa ID, tách version, expand_range, id_to_dirname
│   ├── discovery.py          # Atom API lô 250 ID, tận dụng <updated>, xác nhận sub-batch 50 (T3)
│   ├── source.py             # download_source (RAM <=96 MiB, Range 206 resume), extract_tex_bib (_FastGzipStream 512 KiB + dir cache)
│   ├── s2.py                 # POST /paper/batch (100 ID) + fallback GET /references (limit 1000)
│   ├── manifest.py           # SQLite manifest + atomic_write
│   ├── pipeline.py           # Điều phối Producer-Consumer + Pool extract-worker (E1-E5) + Fast-Defer Queue + --s2-only + OS mutex
│   ├── checkpoint.py         # Sao lưu nền nguyên tử (.tar.gz / mirror) + shrink guard + restore
│   ├── metrics.py            # Đo stage, ResourceSampler O(1), redact_secrets, source_code_hash, summarize_sessions
│   └── export.py             # Tổng hợp bảng Hugging Face & thống kê báo cáo (cross_version_dedup + benchmark_summary + tương thích backup cũ)
├── run_parse.py              # CLI Milestone 2 (Phase 0): khung chạy parser đa nhân
└── parsing/                  # Package xử lý LaTeX & BibTeX (Milestone 2 Phase 0)
    ├── models.py             # Hợp đồng dữ liệu M2 (khóa ở Phase 0)
    └── pipeline.py           # Khung điều phối parse song song & profiler
```

**Phân công phụ trách (Milestone 1):** A — `http`, `manifest`, `pipeline`, `metrics`, `checkpoint` · B — `ids`, `discovery`, `s2` · C — `source`, fixtures · D — `export`, `corpus` · E — `validate_corpus`, Jupyter Book.

### 3.2. Năm nhóm luồng thực thi trong một phiên `pipeline.run()`

| Luồng (Thread) | Vai trò chính | Cô lập tài nguyên |
| --- | --- | --- |
| **1. `MainThread` (arXiv Producer)** | Chia chunk ID; gọi `discover` & `fetch_revised_dates` (lô **250** ID); tải `/src/{id}vK` theo chunk `256 KiB` (`512 KiB` cho gói $\ge 25\text{ MiB}$) vào `bytearray` RAM ($\le 96\text{ MiB}$), tự nối tiếp bằng `HTTP 206 Range` (tối đa **3 lần nối / 4 lượt thử**, nghỉ lịch sự `3,0 → 4,5 → 6,0 s` kèm đổi socket mới); nếu `/src/{id}vK` gặp `429` ở lượt chính thì đẩy ngay bài vào `deferred_ids` và ngủ **Cooldown leo thang `60 → 120 → 240 → 360 → 600 s`**; **đóng HTTP stream & nhả `RateLimiter` ngay lập tức**; đẩy `_ExtractJob` vào `extract_queue` (`maxsize=8`, có điều tiết trần RAM `extract_max_queue_bytes = 192 MiB`). | Chỉ gọi `export.arxiv.org`; không giải nén tar, không gọi S2. |
| **2. Pool `extract-worker` (Consumer, mặc định 2 luồng — `E3`)** | Lấy gói từ `extract_queue`; đọc qua `_FastGzipStream` (`512 KiB` zero-copy `memoryview` + `zlib.decompressobj(16 + zlib.MAX_WBITS)` ở tầng C) và giải nén **1-pass** gzip/tar (`bufsize=512 KiB`, cache thư mục `created_dirs`) chỉ giữ `.tex`/`.bib`/`.bbl` vào `.vK.part/tex/`, tính `sha256`; giải phóng ngay hạn mức byte RAM; ghi `meta.json`, hoán đổi nguyên tử sang `vK/`; ghi `manifest.sqlite` **sau cùng**; định kỳ mỗi **60 s** gọi `_flush_metrics`; giảm bộ đếm nguyên tử `paper_pending_versions[arxiv_id]` và chỉ luồng nào hoàn tất phiên bản cuối cùng của bài (`rem == 0`) mới đẩy `_S2Job` sang `s2_queue`. | Thuần CPU + Disk I/O; giải nén song song trên nhiều nhân CPU (vì `zlib` và file I/O nhả GIL), chạy song song 100% với lượt tải mạng kế tiếp. |
| **3. `s2-worker`** | Gom tối đa **100 bài** từ `s2_queue` (chờ gom 0,35 s); gọi `POST /paper/batch`; gọi fallback phân trang `/references` nếu bị cắt; ghi `references.json` vào mọi `vK/`, cập nhật `venue` cho `paper.json`, và ghi `s2_references.raw.json.gz` làm marker hoàn tất. | Chỉ gọi `api.semanticscholar.org`. |
| **4. `checkpoint-worker`** | Được kích hoạt ở nền mỗi khi đi qua `CRAWLER_BACKUP_EVERY_IDS` ID mới, chạy **song song trong lúc ngủ cooldown `429`** (chỉ khi chữ ký `(final_pairs, s2_done)` thay đổi; bỏ qua tức thì nếu dữ liệu chưa đổi — `skip_if_unchanged=True`), và chạy đồng bộ 1 lần cuối khi kết thúc: chụp snapshot SQLite bằng `sqlite3.Connection.backup`, đóng gói `papers/`, `logs/`, `stats/` ra `.tar.gz` (hoặc thư mục đích) kèm kiểm tra chống tụt lùi dữ liệu (`shrink protection`). | Không chặn `MainThread` (trừ bước `extract_queue.join()` ngắn trước khi mở snapshot). |
| **5. `resource-sampler`** | Luồng daemon của `metrics.ResourceSampler`: đo RSS RAM (mỗi 5 s) và dung lượng đĩa (mỗi 300 s) ở nền, giúp `sampler.snapshot()` đọc cực trị `O(1)` không tốn chi phí quét tiến trình trên luồng chính. | Chỉ đọc thông số hệ thống ở nền. |

---

## 4. Luồng xử lý End-to-End (`run_crawl.py`)

```mermaid
flowchart TB
  subgraph CLI["Khởi tạo & Khóa bảo vệ"]
    R["run_crawl.py: expand_range 25.000 ID, khử trùng base ID, gắn SecretRedactingFilter"]
    R --> LOCK["_acquire_workspace_lock (OS mutex) + nạp base_metrics & rate_state.json"]
  end
  LOCK --> MODE{"Chế độ --s2-only?"}
  MODE -->|Có| S2ONLY["_crawl_s2_only: quét paper.json thiếu S2 -> đẩy thẳng vào s2_queue"]
  MODE -->|Không| CH

  subgraph Main["1. MainThread — arXiv Network Producer (Start-to-Start >= 3.0s)"]
    CH["Chia chunk: min(2 * discovery_batch_size = 500, backup_every_ids)"]
    CH --> PLAN["_plan_chunk: đọc paper.json sẵn có (heal revised_dates) hoặc gom lô mới"]
    PLAN --> DIS["discover (lô 250) + xác nhận missing (sub-batch 50) + fetch_revised_dates"]
    DIS --> PJ["Ghi nguyên tử paper.json / đánh dấu not_found"]
    PJ --> DL["download_source (/src/{id}vK, chunk 256/512 KiB, HTTP 206 Range <= 4 lượt, retry >= 3.0s)<br/>Ngắt sớm PDF/ZIP ở 512 B | Nạp bytearray RAM <= 96 MiB"]
    DL --> H429{"HTTP 429 hoặc hết 4 lượt đứt stream?"}
    H429 -->|Không - 2xx/404| REL["Reset consecutive_arxiv_429 = 0, note_success()<br/>10 lượt 2xx sạch -> hạ 1 bậc streak; nhả RateLimiter ngay"]
    H429 -->|Có - Lượt chính| FD["Defer Queue (U1 + v3.5): note_429() (+0.15s, trần 3.30s)<br/>Đẩy bài vào deferred_ids"]
    FD --> COOL["Escalating Cooldown (60 -> 120 -> 240 -> 360 -> 600s):<br/>recycle_session() + backup nền song song (chỉ khi có dữ liệu mới)"]
    COOL -.-> DL
    REL --> EQ[("extract_queue (maxsize = 8, <= 192 MiB RAM)")]
    EQ -.->|Hết dải chính & có deferred_ids| DEFPASS["Lượt vét cuối dải (in_deferred_pass = True):<br/>Chạy lại _run_chunks cho deferred_ids"]
  end

  subgraph EX["2. Pool extract-worker (2 luồng) — CPU & Disk Consumer (v3.4 Extract-Turbo)"]
    EQ --> EXT["extract_tex_bib (_FastGzipStream 512 KiB + 1-pass tar + dir cache, lọc .tex/.bib/.bbl, sha256)"]
    EXT --> INST["_install_version (fast-path): ghi .vK.part/meta.json -> os.replace sang vK/"]
    INST --> MAN["manifest.record(meta) [GHI SAU CÙNG]"]
    MAN --> FL60["Mỗi 60s: _flush_metrics -> logs/metrics.json"]
    MAN --> LAST{"paper_pending_versions == 0 &<br/>chưa có raw.gz?"}
  end

  LAST -->|Có| SQ[("s2_queue")]
  S2ONLY --> SQ
  subgraph S2["3. s2-worker — Semantic Scholar Batch"]
    SQ --> COL["Gom tối đa 100 bài (cửa sổ 0.35s)"]
    COL --> BAT["POST /graph/v1/paper/batch"]
    BAT --> FB{"referenceCount ><br/>len(refs)?"}
    FB -->|Có| PAG["GET /paper/{id}/references (limit=1000)"]
    FB -->|Không| STORE["Ghi references.json (mọi vK) + venue + s2_references.raw.json.gz"]
    PAG --> STORE
  end

  subgraph CKPT["4. checkpoint-worker — Sao lưu nền"]
    JOIN["Cuối chunk / trước cooldown: extract_queue.join() + _flush_metrics"]
    JOIN --> BK["backup_data_dir -> arxiv-data.tar.gz + .meta.json (shrink guard)"]
  end

  DL -.->|Cuối mỗi chunk| JOIN
  COOL -.-> JOIN
```

### 4.1. Khám phá siêu dữ liệu (Discovery — tối ưu O2 + T3)

1. **Chia chunk:** Danh sách ID được duyệt theo từng chunk kích thước `2 * discovery_batch_size = 500` ID (hoặc nhỏ hơn nếu `backup_every_ids > 0`).
2. **Kiểm tra trạng thái cục bộ (`_plan_chunk`):**
   - Nếu đã có `data/papers/<dirname>/paper.json`: đọc lại `PaperMeta`. Nếu bài có `num_versions >= 2` nhưng `revised_dates` chưa đủ `(num_versions - 1)` phần tử (do lần chạy trước bị gián đoạn), đưa vào danh sách **auto-heal** để gọi bổ sung `fetch_revised_dates`.
   - Nếu `manifest.sqlite` đã ghi `(arxiv_id, 1)` là `not_found`: bỏ qua ngay.
   - Các ID còn lại được gom vào danh sách cần khám phá.
3. **Gọi Atom API theo lô 250 (`discover` — T3):**
   - Gửi `GET /api/query?id_list=...&max_results=250` (`discovery_batch_size = 250`).
   - Từ mỗi `<entry>`, tách hậu tố `vN` của `<id>` làm tổng số phiên bản `num_versions = N`; `<published>` là `submission_date` (ngày của `v1`); `<updated>` là ngày của phiên bản mới nhất `vN`.
   - **Tận dụng `<updated>` (O2):** Với bài có đúng `N = 2` phiên bản, `<updated>` chính là ngày của `v2` $\rightarrow$ điền luôn `revised_dates = [updated]`, tiết kiệm 100% truy vấn phụ cho các bài 2 phiên bản! Với bài `N >= 3`, giữ `<updated>` cho `vN` và chỉ cần hỏi thêm `v2 .. v{N-1}`.
4. **Xác nhận ID vắng mặt theo sub-batch 50 (T3):**
   - Các ID không xuất hiện trong kết quả lô 250 được kiểm tra lại theo từng cụm **50 ID** (`_CONFIRM_BATCH_SIZE = 50`; chỉ tách lẻ từng ID nếu feed trả về `/api/errors`). Nếu vẫn không tồn tại $\rightarrow$ ghi manifest `(id, 1) = not_found` (không tạo thư mục bài).
5. **Lấy ngày phiên bản trung gian (`fetch_revised_dates`):**
   - Gom các định danh `idv2 .. idv{N-1}` vào các lô 250 (kèm cơ chế re-check sub-batch 50 nếu thiếu). Nếu sau khi re-check vẫn khuyết ngày của phiên bản nào, giữ chuỗi rỗng `""` tại vị trí đó và tăng bộ đếm `revised_dates_incomplete` (vẫn tiếp tục tải gói nguồn).
6. **Xử lý Rate-Limit trong Discovery & Lưu đĩa:**
   - Nếu Atom API trả về `HTTP 429` trong lúc `discover` hoặc `fetch_revised_dates` (`fast_defer=False`), hệ thống chờ `Retry-After` (hoặc backoff bậc thang `15 s → 30 s → 60 s`) và thử lại tối đa 4 lần; nếu vẫn bị chặn khi bật `--auto-resume-rate-limit`, toàn bộ chunk đó được đẩy vào `deferred_ids` kèm nghỉ cooldown `180 s`.
   - Khi thành công: ghi nguyên tử `data/papers/<dirname>/paper.json`.

### 4.2. Tải gói nguồn, Defer + Cooldown Leo thang, Retry Range Lịch sự & Giải nén Đa luồng `v3.4/v3.5`

Với mỗi bài hợp lệ, duyệt `version = 1 .. paper.num_versions`:

**A. Trên `MainThread` (`_process_paper` $\rightarrow$ `_download_version` $\rightarrow$ `download_source`):**
1. Đăng ký `(arxiv_id, version)` vào manifest (trạng thái `pending` nếu chưa có). Nếu trạng thái hiện tại đã thuộc nhóm kết thúc (`is_final`: `ok`, `ok_no_tex`, `no_source`, `not_found`, `corrupt`) $\rightarrow$ **bỏ qua**.
2. Gọi `GET /src/{arxiv_id}v{version}` với `stream=True` và đọc theo khung **`256 KiB`** (tự động nâng lên **`512 KiB`** khi `Content-Length >= 25 MiB` — **T5**).
   - **Sàn nghỉ hậu tải `min_idle_gap = 0,50 s` & Xoay vòng TCP mỗi `400` request (`v3.3`):** Với `~96%` gói nguồn thông thường (tải trong $\le 2,50\text{ s}$), chu kỳ `Start-to-Start = 3,00 s` giữ nguyên tuyệt đối (`+0,00 s` overhead). Với `~4%` bài nặng kéo dài `> 2,50 s`, `RateLimiter.wait()` đảm bảo socket nghỉ tối thiểu `0,50 s` tính từ `_last_done_at` (`Duty Cycle <= 85%`), kết hợp tự động làm mới `requests.Session` (`recycle_session()`) ngay cuối request thứ `400` (trước khi ngủ chờ nhịp `3,00 s`, tốn `0 ms` overhead) để xóa trạng thái theo dõi kết nối dài hạn trên CDN Varnish/Fastly.
3. **Ngắt sớm ở 512 bytes đầu (`O5` / `S2`):**
   - Ngay khi đọc đủ 4–512 bytes đầu tiên, kiểm tra magic bytes. Nếu là **`%PDF`** hoặc **`ZIP`** (`PK\x03\x04`, `PK\x05\x06`, `PK\x07\x08`): ngắt luồng tải ngay lập tức (nếu phần còn lại của phản hồi $\le 64\text{ KiB}$ thì đọc nốt để tái sử dụng kết nối TCP Keep-Alive), giữ lại đúng 512 bytes stub và ghi nhận `raw_archive_bytes` từ `Content-Length`.
4. **Đệm RAM Zero-Copy (`spool_max_bytes = 96 MiB`) & Nối tiếp `HTTP 206 Range` tuân thủ nghiêm quy tắc $\ge 3,0\text{ s}$ (`T2` + `v3.5`):**
   - Mọi gói nguồn $\le 96\text{ MiB}$ (>99,5% tổng số bài trên arXiv) được nối trực tiếp vào `bytearray` trong RAM — **0 byte ghi xuống `data/.tmp/`**. Chỉ những gói khổng lồ $> 96\text{ MiB}$ mới tự động chuyển sang file tạm trong `data/.tmp/`.
   - **Nối tiếp luồng đứt lịch sự & giới hạn số lượt thử (`Polite Range Resume`):** Nếu luồng tải bị đứt giữa chừng (`ChunkedEncodingError` / `ConnectionError` / `Timeout`), `download_source` giữ nguyên các byte đã tải trong `mem_buf` (hoặc file tạm), báo `client.note_stream_break()`, **đóng ngay socket bị bóp băng thông (`client.recycle_session()`)**, và kiểm tra số byte tiến thêm `progressed = written - written_before`:
     - Đặt thời gian chờ `End-to-Start` tối thiểu bằng `_stream_retry_backoff_seconds`: **`3,0 s → 4,5 s → 6,0 s`** tính từ lúc đóng luồng lỗi (`_last_done_at`), đảm bảo **không bao giờ retry dưới `3,0 s`** kể cả khi luồng lỗi trước đó đã kéo dài `> 3,0 s` và `stalled_attempts == 0`.
     - Nếu `progressed >= 512 KiB`: reset `stalled_attempts = 0` và gửi `Range: bytes={written}-` trên socket mới, nhưng giới hạn cứng **`_MAX_TOTAL_STREAM_ATTEMPTS = 4` lượt thử / phiên bản** (`1` lượt gốc + tối đa **`3`** lần `Range` retry; hoặc dừng sớm khi `stalled_attempts >= 2` với tiến độ `< 512 KiB`) để không bao giờ bắn dồn 5–11 request `GET` trên cùng một gói đang bị CDN cắt kết nối.
     - Nếu hết 4 lượt thử trong lượt chạy chính (`auto_resume_rate_limit=True`), `_process_paper` tự động đẩy bài đó vào `deferred_ids` để tải lại ở cuối dải (và nếu $\ge 2$ bài liên tiếp đứt stream thì kích hoạt nghỉ cooldown bảo vệ IP).
5. **Defer tức thì + Cooldown Leo thang thông minh & Backup Nền không chặn (`U1` + `v3.5`):**
   - Khi `/src/{id}vN` trả về `429`, `_wait_arxiv_429(..., fast_defer=True)` tăng `arxiv_429`, gọi `limiter.note_429()` nhích nhịp **`+0,15 s`** (trần cứng **`3,30 s`**), và đặt `limiter.schedule_retry_backoff(limiter._min_interval)` tính từ `_last_done_at`.
   - **Trong lượt chạy chính (`not run.in_deferred_pass` và `auto_resume_rate_limit=True`):**
     - **Không còn `"0 s extra wait"`:** Ngay từ lần `429` đầu tiên, đẩy ngay `paper.arxiv_id` vào `deferred_ids` (không đứng chờ 15 s tại chỗ) **và** gọi `_handle_auto_resume_cooldown` để tránh bắn tiếp ID kế tiếp vào IP đang bị chặn.
     - **Cooldown leo thang (`_compute_arxiv_cooldown`):** Thời gian nghỉ tăng theo chuỗi `arxiv_429_streak` với hệ số `(1, 2, 4, 6, 10)` trên nền `60 s` $\rightarrow$ **`60 s → 120 s → 240 s → 360 s → 600 s`** (tôn trọng header `Retry-After` nếu lớn hơn, trần `600 s`). Sau khi hết cooldown, **giữ nguyên** `arxiv_429_streak` và nhịp thích ứng `3,15–3,30 s` để nếu IP vẫn còn nóng thì lần `429` kế tiếp leo thẳng lên bậc cao hơn thay vì lặp mãi `60 s`.
     - **Hạ bậc dần khi mạng ổn định:** Mỗi lượt tải `/src/` trả `2xx` sạch tăng `arxiv_ok_since_429 += 1`; cứ đủ **`10` lượt `2xx` sạch liên tiếp** thì `arxiv_429_streak` mới giảm `1` bậc về `0`.
     - **Không phạt oan bài tải thành công:** Phản hồi `2xx` có header chậm không cộng điểm `thermal_score` (mỗi `2xx` giảm `-0,5`) và pipeline tuyệt đối **không** kích hoạt ngủ tản nhiệt `60 s` sau khi đã tải xong một phiên bản `ok`/`no_source`.
     - **Sao lưu thông minh song song lúc ngủ cooldown (`skip_if_unchanged=True`):** Trước khi ngủ cooldown, pipeline so sánh chữ ký tiến độ `_workspace_progress_signature(run) = (final_pairs, s2_done)` với lần backup gần nhất (`last_backup_signature`, được khởi tạo sẵn từ `.meta.json` khi mở phiên): nếu **không có dữ liệu mới** thì **bỏ qua backup ngay lập tức (`0 s` I/O)**; nếu có dữ liệu mới thì giao cho `checkpoint-worker` đóng gói **song song ở nền** ngay trong lúc `MainThread` đang ngủ cooldown (thay vì chờ `~5 phút` nén `tar.gz` xong mới ngủ thêm `180 s`).
   - **Lượt vét cuối dải (`in_deferred_pass = True`):** Sau khi đi hết dải ID chính, nếu `deferred_ids` khác rỗng, hệ thống đợi `extract_queue.join()`, khử trùng lặp danh sách `deferred_ids` và chạy lại `_run_chunks(retry_list, is_deferred=True)`. Ở lượt vét này, nếu vẫn gặp `429`, `_wait_arxiv_429` chờ theo `Retry-After` / stepped backoff (`15 s → 30 s → 60 s`, tối đa 4 lần thử).
6. **Nhả kết nối tức thì & Điều tiết RAM theo byte (`E4`):** Ngay khi tải xong byte cuối (hoặc ngắt ở 512 B), khối `with response:` đóng lại, giải phóng slot `RateLimiter` để nhịp `Start-to-Start 3,0 s` của gói kế tiếp không bị trễ bởi thời gian giải nén. Các gói $\ge 50\text{ MiB}$ hoặc sự kiện đứt stream/retry được ghi tự động vào `data/logs/diagnostics.jsonl` (**U5**). Trước khi đẩy `_ExtractJob` vào `extract_queue`, hàm `_enqueue_extract_job` kiểm tra hạn mức `extract_max_queue_bytes = 192 MiB` (`CRAWLER_EXTRACT_MAX_QUEUE_BYTES`, luôn cho phép ít nhất 1 gói đi qua để tránh deadlock) nhằm giữ trần RAM Colab an toàn tuyệt đối khi gặp chuỗi gói nặng liên tiếp.

**B. Trên Pool `extract-worker` (`_consume_extract_job` $\rightarrow$ `extract_tex_bib` — tối ưu `v3.4 Extract-Turbo E1–E5`):**
1. **Giải nén luồng lớn `_FastGzipStream` (`512 KiB` — `E1`) & Giải nén 1-pass mở Gzip duy nhất 1 lần (`O4`):**
   - Thay thế `gzip.GzipFile` mặc định của CPython (vốn đọc từng mảnh nhỏ `8 KiB` trong `_compression.DecompressReader` và tính `zlib.crc32` ở tầng Python, tốn ~12.800 vòng lặp cho mỗi gói 100 MB) bằng `_FastGzipStream`: cắt trực tiếp khối **`512 KiB`** từ `memoryview` zero-copy (hoặc file tạm có `buffering=512 KiB`) đưa vào `zlib.decompressobj(16 + zlib.MAX_WBITS)` (kiểm tra header Gzip + trailer 8-byte CRC32/ISIZE ngay trong mã C, hỗ trợ cả các luồng multi-member gzip nối tiếp).
   - Kiểm tra 512 bytes đầu: nếu là `%PDF` hoặc `ZIP` $\rightarrow$ trả về `Status.NO_SOURCE`.
   - Nếu là Gzip (`\x1f\x8b`): mở `_FastGzipStream` **một lần duy nhất**, đọc thử 512 bytes giải nén đầu tiên để kiểm tra header tar (`ustar` hoặc checksum octal hợp lệ) rồi ghép lại bằng `_PrefixedReader` để truyền thẳng vào `tarfile.open(fileobj=..., mode="r|", bufsize=512*1024)` hoặc bộ ghi file `.tex` đơn.
2. **Lọc `.tex`/`.bib`/`.bbl` (`U4`), Cache thư mục cha (`E2`) & An toàn giải nén:**
   - Chặn triệt để path traversal (`..`, đường dẫn tuyệt đối, tên thiết bị Windows `CON`/`NUL`..., symlink/hardlink/device) và giới hạn dung lượng giải nén `max_extract_bytes = 2 GiB` (chống zip-bomb).
   - Với member không phải `.tex`/`.bib`/`.bbl` (ảnh `.png`, `.pdf`, `.eps`...): cộng `member.size` vào `raw_uncompressed_bytes` và **bỏ qua ngay ở mức stream `512 KiB`** (không đọc xuất ra đĩa, không băm `sha256`).
   - Với member **`.tex`, `.bib` hoặc `.bbl` (file bibliography đã biên dịch phụ trợ — U4)**: cache tập thư mục đã tạo `created_dirs` để chỉ gọi `mkdir(parents=True, exist_ok=True)` và `parent.resolve().is_relative_to(stage_root)` đúng **1 lần trên mỗi thư mục con mới** (loại bỏ 95–99% syscall NTFS/Linux thừa trên các bài có hàng trăm file `.tex` con); vừa ghi vào `.vK.part/tex/<relative_path>`, vừa băm `sha256`. Nếu trong cùng một gói tar có 2 member trùng đường dẫn (ví dụ `JSSC.bib`), member sau **ghi đè an toàn** lên member trước theo đúng chuẩn POSIX tar (`S3`).
   - Cuối luồng tar: đọc cạn các block đệm 0 còn lại theo khối `512 KiB` trên cùng luồng `_FastGzipStream` để kiểm tra trọn vẹn trailer CRC32/ISIZE mà không cần mở file lần hai. Ngay trong khối `finally` của bước giải nén, giải phóng `download.data = None` và trả lại hạn mức `extract_queued_bytes` (`_release_queued_bytes`).
   - Nếu gói hợp lệ nhưng không có file nguồn nào $\rightarrow$ `Status.OK_NO_TEX`; nếu có $\ge 1$ file $\rightarrow$ `Status.OK`; nếu archive hỏng hoặc vi phạm an toàn $\rightarrow$ `Status.CORRUPT`.
3. **Cài đặt nguyên tử Fast-Path (`E5`), Ghi Manifest sau cùng & Đồng bộ Đa luồng (`E3`):**
   - Ghi `.vK.part/meta.json` (kèm `version_date`, `crawled_at`, `s2_scope="paper"`). Nếu thư mục `vK/` chưa tồn tại (99,9% trường hợp crawl mới), thực hiện thẳng `part.replace(final)` mà không tốn syscall kiểm tra/xóa `.vK.old`; nếu `vK/` đã tồn tại từ lần chạy trước thì bảo toàn `references.json` và hoán đổi qua `.vK.old`.
   - **Ghi `manifest.sqlite` ở bước cuối cùng:** đảm bảo nếu máy tắt giữa chừng, lần chạy sau sẽ tự động dọn `.vK.part` (bằng `os.scandir` nhanh) và tải lại sạch sẽ.
   - **Bộ đếm hoàn tất bài nguyên tử (`paper_pending_versions` — `E3`):** Với `extract_workers = 2` (cấu hình qua `CRAWLER_EXTRACT_WORKERS`), hai phiên bản `v1` (rất nặng) và `v2` (nhẹ) của cùng một bài được giải nén song song trên 2 nhân CPU vào `.v1.part` và `.v2.part` độc lập. Khi mỗi worker hoàn tất một phiên bản, nó giảm `paper_pending_versions[arxiv_id]` đi 1 dưới khóa; chỉ worker nào giảm bộ đếm về **đúng `0`** mới tăng `papers_done` và đẩy `_S2Job` sang `s2_queue`, đảm bảo S2 luôn ghi `references.json` vào đầy đủ mọi phiên bản.

### 4.3. Semantic Scholar — Worker nền theo lô (`s2-worker`) & Chế độ `--s2-only` (`U2`)

1. **Điều kiện vào hàng đợi (`s2_queue`):**
   - Trong luồng crawl chuẩn: bài đã hoàn tất xử lý mọi phiên bản `1..num_versions` (do `extract-worker` đẩy vào khi xử lý xong phiên bản cuối, hoặc do `MainThread` đẩy vào khi resume các bài đã `is_final` trên manifest) **và** chưa có file marker `data/papers/<dirname>/s2_references.raw.json.gz` (hoặc còn thiếu `references.json` ở bất kỳ `vK/` nào).
   - **Trong chế độ độc lập `--s2-only` (`_crawl_s2_only` — U2):** Bỏ qua 100% các lệnh gọi mạng tới `export.arxiv.org`, chỉ quét nhanh các thư mục bài đã có `paper.json` trên đĩa nhưng thiếu trích dẫn S2 và đẩy thẳng vào `s2_queue`.
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
│   ├── crawl.log                       # Log chi tiết (đã lọc qua SecretRedactingFilter tự che API key/token)
│   ├── metrics.json                    # Thời gian từng stage, đỉnh RAM/Disk, sessions, git_commit, source_code_hash
│   ├── diagnostics.jsonl               # Sự kiện bất thường (HTTP 429/403/5xx, đứt stream, gói >= 50 MiB) (U5)
│   └── rate_state.json                 # Lưu mốc cooldown (arxiv_cooldown_until, s2_cooldown_until) liên tiến trình (U3)
├── .tmp/                               # Thư mục tạm (trống >99,5% thời gian nhờ đệm RAM 96 MiB)
├── papers/
│   └── <dirname>/                      # Tên thư mục từ ids.id_to_dirname() (VD: 2408.01045)
│       ├── paper.json                  # PaperMeta (title, authors, submission_date, revised_dates, venue...)
│       ├── s2_references.raw.json.gz   # Phản hồi S2 gốc nén gzip (marker đã xong S2)
│       ├── v1/
│       │   ├── meta.json               # VersionMeta (status, raw/kept bytes, danh sách files kèm sha256)
│       │   ├── references.json         # Danh sách Reference từ S2
│       │   └── tex/                    # Cây thư mục chỉ chứa các file .tex, .bib và .bbl giữ nguyên cấu trúc
│       └── v2/
│           └── ...
└── stats/                              # Sinh ra bởi src/export_data.py
    ├── papers.parquet / papers.csv     # Bảng papers chuẩn bị cho Hugging Face (Milestone 3)
    ├── references.parquet / .csv       # Bảng references (chỉ giữ tham khảo có ref_arxiv_id)
    ├── summary.json                    # Thống kê báo cáo M1 + cross_version_dedup + benchmark_summary (U6)
    └── performance.json                # Tóm tắt hiệu năng (wall time, active stage seconds, RAM/Disk peak)
```

---

## 6. Cơ chế Resume, Auto-Resume 429, Checkpoint nền & Live Telemetry

| Tình huống vận hành | Hành vi của hệ thống |
| --- | --- |
| **Ngắt giữa chừng (Ctrl+C / mất kết nối Colab)** | Khối `finally` của `pipeline.run()` đợi `extract-worker` và `s2-worker` kết thúc an toàn, dừng `ResourceSampler`, flush `metrics.json`, và chạy một bản backup cuối (nếu cấu hình). Khi chạy lại **cùng câu lệnh**: các cặp `(id, version)` đã `is_final` được bỏ qua ngay; cặp `failed`/`pending` được tải lại; bài thiếu `s2_references.raw.json.gz` được đưa lại vào `s2_queue`. |
| **Tự động vượt qua đợt phạt 429 — Defer + Cooldown Leo thang (`--auto-resume-rate-limit` — `U1` + `v3.5`, bật mặc định qua CLI/env)** | Khi một request `/src/{id}vN` gặp `429` (hoặc đứt stream hết 4 lượt thử) ở lượt chính:<br/>1. **Defer tức thì (không chờ 15 s tại chỗ, không bắn bồi `"0 s extra wait"`):** Đẩy ngay ID đó xuống hàng đợi `deferred_ids` để bảo vệ IP.<br/>2. **Cooldown leo thang thông minh (`_compute_arxiv_cooldown`):** Đóng socket cũ (`recycle_session()`), đợi `extract_queue.join()`, flush `metrics.json`, chỉ kích hoạt `checkpoint-worker` đóng gói **song song ở nền** nếu `(final_pairs, s2_done)` có thay đổi (bỏ qua `0 s` nếu dữ liệu chưa đổi), lưu `rate_state.json`, và ngủ **`60 s → 120 s → 240 s → 360 s → 600 s`** theo `arxiv_429_streak` (chỉ hạ `1` bậc sau **10** lượt `2xx` sạch liên tiếp; giữ nhịp thích ứng `3,15–3,30 s` sau khi dậy).<br/>3. **Lượt vét cuối dải:** Sau khi đi hết dải chính, tự động quay lại tải sạch các bài trong `deferred_ids`. |
| **Lưu Cooldown liên tiến trình & Khóa OS Mutex (`U3`)** | • File `data/logs/rate_state.json` lưu mốc thời gian phạt còn dư (`arxiv_cooldown_until`, `s2_cooldown_until`); nếu người dùng tắt và bật lại lệnh ngay lúc đang bị phạt, tiến trình mới tự động chờ hết thời gian phạt trước khi gọi API.<br/>• Hàm `_acquire_workspace_lock` tạo khóa tiến trình cấp hệ điều hành tại `tempfile.gettempdir()` giúp chặn tuyệt đối lỗi chạy trùng 2 tiến trình trên cùng thư mục `data/`. |
| **Checkpoint tự động lên Google Drive (`O6` + `v3.5`)** | Khi đặt `--backup-dir` (hoặc `CRAWLER_BACKUP_DIR`) và `--backup-every-ids N` (trên Colab thường đặt `N = 1000`):<br/>• Khi khởi động lại (resume), hàm `_count_leading_done_ids` đếm số ID đầu dải đã hoàn tất cả arXiv lẫn S2 để đặt mốc `checkpoint_last_seen` và nạp chữ ký backup hiện có từ `.meta.json`, tránh backup lại ngay lập tức.<br/>• Cứ mỗi `N` ID mới đi qua (hoặc khi ngủ cooldown `429` có dữ liệu mới), luồng chính gọi `extract_queue.join()` + `_flush_metrics`, rồi bật `checkpoint-worker` nén streaming sang `arxiv-data.tar.gz` ở nền.<br/>• **Shrink Protection & Skip Unchanged (`checkpoint.py`):** Kèm file cạnh `arxiv-data.tar.gz.meta.json` lưu `(final_pairs, total_pairs, s2_raw_files)`. Bản backup mới **không bao giờ ghi đè** lên bản backup cũ nếu ít cặp hơn, và tự động **bỏ qua 100% I/O nén lại** khi `skip_if_unchanged=True` mà dữ liệu không đổi. |
| **Live Metrics Checkpointing (`V3-3`) & Tổng hợp đa phiên (`U6` + `summarize_sessions`)** | • `pipeline.py` nạp `base_metrics` lúc bắt đầu và gọi `metrics.flush_run` **mỗi 60 giây trên `extract-worker`**, **cuối mỗi chunk**, **trước mỗi lần backup**, và **trong khối `finally`** mà không bị cộng trùng (`idempotent` trong cùng phiên), tự động lưu đầy đủ thông tin phần cứng/môi trường (`colab_cpu`, `cpu_count_logical`, `python_version`), `run_id`, `stats_sha256`, `source_revision` và tài nguyên theo từng phiên.<br/>• Hàm `summarize_sessions()` / `aggregate_sessions()` tự động khử trùng lặp lịch sử các phiên chạy Colab (`duplicate_run_files_skipped`), phát hiện chồng lấp thời gian (`sessions_overlap`), loại bỏ khoảng thời gian chết (`idle_gap_seconds`) giữa hai phiên, tính trung bình RAM gia quyền theo thời gian chạy (`session_time_weighted_avg_rss_mib`), và bóc tách tường minh **Thời gian xử lý thực (`active_wall_seconds` / `total_profiler_sec`)** so với **Thời gian chờ phạt/Retry (`cooldown_retry_seconds`)** trong `summary.json` (`benchmark_summary`). |
| **Tương thích ngược 100% với Backup cũ (`export.py` + `checkpoint.py` + `export_data.py`)** | • Cho phép xuất dữ liệu trực tiếp từ file nén backup (`python src/export_data.py --data-dir arxiv-data.tar.gz` hoặc `--backup arxiv-data.tar.gz`), tự động nhận diện thư mục lồng nhau (`resolve_data_root`: `data/`, `arxiv-data/`, `content/data/`).<br/>• Tự động chuẩn hóa các bản backup cũ (`v0`–`v3.2`) có schema JSON đời trước (`first_submitted_date`, `selected_files_bytes`, `extracted_total_bytes`, `references.json` dạng `dict` hoặc đặt ở gốc bài, `metrics.json` chưa có mảng `sessions` hoặc chỉ lưu `resources` ở cấp cao nhất) và tự động bỏ qua an toàn các bài dở dang (`allow_incomplete=True`) trong bản backup ngắt giữa chừng. |
| **Khử trùng lặp xuyên phiên bản (`V3-4`)** | Khi chạy `export_data.py`, module `export.py` đối chiếu mã băm `sha256` của từng file `.tex`/`.bib`/`.bbl` giữa `v1..vN` để xuất trường `cross_version_dedup` trong `summary.json` (số phiên bản giữ nguyên 100% nội dung nguồn, số file và số byte trùng lặp), nối trực tiếp sang bài toán Deduplication của Milestone 2. |

---

## 7. Công thức và Ước lượng Thời gian

Ký hiệu: $P$ là số bài cần crawl ($5.000$ ID/dải hoặc $25.000$ ID toàn cục); $\bar{V}$ là số phiên bản trung bình mỗi bài (thực tế dải lớn $\bar{V} \approx 1,64 \implies R \approx 8.187$ phiên bản, hoặc theo pilot $\bar{V} \approx 1,90 \implies R \approx 9.500$ phiên bản); $R = \sum_{i=1}^{P} V_i$ là tổng số phiên bản.

1. **Số request tới `export.arxiv.org`:**
   $$N_{\text{arxiv}} = N_{\text{discovery\_batches}} + N_{\text{confirm\_subbatches}} + N_{\text{revised\_batches}} + R$$
   Với lô `250` ID, sub-batch `50` ID (`T3`) và tối ưu `<updated>` (`O2`), phần discovery cho $5.000$ ID chỉ tốn $\approx 20 + 5 + 5 \approx 30$ requests ($\approx 1,5\text{ phút}$).

2. **Thời gian tầng arXiv (`Start-to-Start >= 3,0 s` + Producer–Consumer `v3.1/v3.2/v3.3`):**
   - Sàn lý thuyết của bộ điều nhịp: $T_{\text{floor}} = 3,0\text{ s} \times N_{\text{arxiv}}$.
   - Nhờ nhịp **Start-to-Start**, sàn nghỉ hậu tải `min_idle_gap = 0,50 s` (chỉ kích hoạt cho các bài tải `> 2,50 s`, `+0,00 s` overhead cho `96%` bài thông thường) và giải nén tách sang `extract-worker`, mọi gói nguồn có thời gian tải mạng $\le 2,50\text{ s}$ đều đạt chu kỳ đúng $3,00\text{ s/request}$; các gói PDF/ZIP được ngắt ngay ở byte 512 chỉ mất $\sim 0,1\text{ s}$ mạng.
   - **Cho 1 dải 5.000 ID ($R \approx 8.187$ phiên bản, sàn lý thuyết `6,85 giờ`):**
     - **Giờ trống / ít 429 (`10:00 – 18:00` giờ VN):** chu kỳ trung bình `~3,00 – 3,03 s/phiên bản` $\rightarrow$ **`~6,85 – 6,92 giờ` (< 7,0 giờ)**.
     - **Giờ cao điểm / chạy liên tục nhiều giờ (`19:00 – 03:00` giờ VN):** chu kỳ trung bình `~3,03 – 3,13 s/phiên bản` $\rightarrow$ **`~6,92 – 7,15 giờ` (< 7,2 giờ)** (loại bỏ hoàn toàn bão `429` dây chuyền và hàng chục phút nén lại `tar.gz` trùng lặp nhờ retry `HTTP 206 Range` lịch sự `3,0 → 4,5 → 6,0 s` tối đa 4 lượt kèm đổi socket, cooldown leo thang `60 → 120 → 240 → 360 → 600 s`, không phạt oan bài tải `ok`, và backup nền song song `skip_if_unchanged=True`).
   - **Cho toàn bộ 25.000 ID ($R \approx 41.000$ phiên bản):** **`~34,3 – 35,8 giờ`** chạy tích lũy tự động 100% (~1,5 ngày treo máy liên tục với `Auto-Resume` + `Thermal-Aware Pacing` + `Backup Google Drive` mỗi 1.000 ID).

3. **Thời gian tầng Semantic Scholar (`s2-worker` chạy song song):**
   $$T_{\text{s2}} \approx 1,1\text{ s} \times \left(\lceil P / 100 \rceil + F_{\text{fallback}}\right)$$
   Do chạy trên luồng riêng song song hoàn toàn với arXiv, $T_{\text{s2}}$ bị che khuất (hidden) bên dưới thời gian chờ arXiv khi có `S2_API_KEY` ổn định. Nếu cần chạy bù độc lập bằng `--s2-only`, 5.000 ID chỉ mất **~3 – 6 phút**.

---

## 8. Hợp đồng dữ liệu (Data Contracts)

- **Milestone 1 (`src/crawler/models.py` — `SCHEMA_VERSION = 1`, bất biến):**
  - `PaperMeta`, `VersionMeta`, `FileEntry` (`path`, `size`, `sha256`), `Reference` (`ref_s2_id`, `ref_arxiv_id`, `ref_title`, `ref_authors`, `ref_submission_date`).
  - Hai bảng xuất cho Hugging Face (nộp ở Milestone 3):
    - `papers`: `arxiv_id, title, authors, submission_date, revised_dates, venue`.
    - `references`: chỉ lọc các tham khảo **có `ref_arxiv_id`**, gồm `arxiv_id, ref_arxiv_id, ref_title, ref_authors, ref_submission_date, ref_s2_id` (trên đĩa cục bộ vẫn giữ 100% tham khảo).
- **Tương thích ngược 100% với dữ liệu backup cũ:** Mọi bản sao lưu `arxiv-data.tar.gz` và `manifest.sqlite` từ các phiên bản trước (`v1`, `v2`, `v3`) đều nạp và chạy tiếp trực tiếp trên mã `v3.1/v3.2` mà không cần migrate.
- **Milestone 2 (`src/parsing/models.py` — đã khóa ở Phase 0 ngày 06/11):**
  - Đọc trực tiếp từ cấu trúc `data/papers/<dirname>/vK/tex/` (bao gồm `.tex`, `.bib` và `.bbl`) và `meta.json` của Milestone 1 để phân rã cây phân cấp LaTeX, chuẩn hóa công thức và trích xuất BibTeX.

---

## 9. Bảng đối chiếu Triển khai thực tế vs Kế hoạch tối ưu (`Plan-toi-uu-luong-crawl-5000-id-vi.md`)

| Mã hạng mục | Nội dung tối ưu | Trạng thái trong code | Chi tiết thực tế trong repo |
| --- | --- | --- | --- |
| **O1 + T5** | Nhịp `Start-to-Start 3,0 s` + 1 kết nối `_SingleConnAdapter` (`TCP_NODELAY`, bảo toàn Linux `tcp_rmem` auto-tuning) | ✅ Đã triển khai | `RateLimiter` & `_SingleConnAdapter` trong `http.py` (`pool_connections=1, pool_maxsize=1, TCP_NODELAY`, không khóa `SO_RCVBUF`) |
| **O2 + T3** | Discovery lô **250 ID** + tận dụng `<updated>` + sub-batch **50 ID** + auto-heal | ✅ Đã triển khai | `discovery.py` (`discovery_batch_size=250`, `_CONFIRM_BATCH_SIZE=50`) + `_plan_chunk` trong `pipeline.py` |
| **O3 + T5** | Tách `extract-worker` + Đệm RAM `bytearray` $\le 96\text{ MiB}$ + `_ZeroCopyBytesReader` | ✅ Đã triển khai | `download_source` nhả socket ngay; đọc qua `memoryview` không nhân bản RAM; chunk `512 KiB` cho gói $\ge 25\text{ MiB}$ |
| **O4 + S3 + U4** | Giải nén `.tar.gz` 1-pass + giữ **`.tex`, `.bib`, `.bbl`** + ghi đè entry trùng tên | ✅ Đã triển khai | `_write_tar_members_from_stream` bỏ qua ảnh, giữ thêm `.bbl` phụ trợ cho M2, xử lý `JSSC.bib` |
| **O5 + S2** | Ngắt sớm stream `/src/` ở 512 bytes cho `%PDF` và `ZIP` | ✅ Đã triển khai | `_EARLY_ABORT_MAGICS` + drain $\le 64\text{ KiB}$ giữ ấm socket |
| **O6 + S7** | Backup nền `checkpoint-worker` + Shrink Guard + Streaming `.tar.gz` | ✅ Đã triển khai | `checkpoint.py` (`backup_data_dir`, `restore_data_dir`, `.meta.json`) |
| **S1–S3 (S2)** | S2 Batch 100 ID + Fallback `/references` + `AdaptiveRateLimiter` | ✅ Đã triển khai | `s2.py`, `_s2_worker` trong `pipeline.py` |
| **V3-3 + U6** | Live Metrics $O(1)$ + Bộ tổng hợp đa phiên Colab `aggregate_sessions()` | ✅ Đã triển khai | `metrics.py` & `export.py` tách bạch `active_wall_seconds`, `cooldown_retry_seconds` và loại bỏ `idle_gap_seconds` |
| **V3-4** | Thống kê trùng lặp nội dung xuyên phiên bản (`cross_version_dedup`) | ✅ Đã triển khai | `export.py` tính trùng lặp `sha256` giữa `v1..vN` trong `summary.json` |
| **U1 (v3.1/v3.5)** | Defer tức thì + Cooldown leo thang (`--auto-resume-rate-limit`) | ✅ Đã triển khai | Đẩy ngay ID bị 429 vào `deferred_ids` và ngủ cooldown leo thang `60→120→240→360→600 s` (hạ 1 bậc sau 10 lượt `2xx` sạch) |
| **U2 (v3.1)** | Chế độ vét & làm giàu Semantic Scholar độc lập (`--s2-only`) | ✅ Đã triển khai | `_crawl_s2_only` trong `pipeline.py` và cờ `--s2-only` trong `run_crawl.py` |
| **U3 (v3.1)** | Lưu trạng thái Cooldown liên tiến trình (`rate_state.json`) & Khóa OS Mutex | ✅ Đã triển khai | `load_rate_state`/`save_rate_state` trong `http.py` và `_acquire_workspace_lock` trong `pipeline.py` |
| **U5 (v3.1)** | Nhật ký chẩn đoán `diagnostics.jsonl`, `redact_secrets()` & `source_code_hash` | ✅ Đã triển khai | Tự động che API key trong log, lưu hash code và ghi sự kiện bất thường vào `data/logs/diagnostics.jsonl` |
| **T1 (v3.2/v3.3)** | Xóa phạt kép 429 (`+0,15 s`, trần `3,30 s`, hồi phục sau **3** bài) & Backoff `15→30→60s` | ✅ Đã triển khai | `RateLimiter` trong `http.py`, `_wait_arxiv_429` và `_wait_s2_429` trong `pipeline.py` |
| **T2 (v3.2/v3.5)** | Nối tiếp `HTTP Range (206 Partial Content)` lịch sự (`3,0→4,5→6,0 s`, tối đa 4 lượt thử + đổi socket) | ✅ Đã triển khai | `download_source` giữ chuẩn $\ge 3,0\text{ s}$ `End-to-Start` cả khi `stalled == 0`, trần 4 lượt thử / phiên bản |
| **T4 (v3.2/v3.3)** | Đồng bộ hóa đồng hồ retry `schedule_retry_backoff` | ✅ Đã triển khai | `RateLimiter.wait()` tính từ cả `_last_start_at` và `_last_done_at` (chặn bắn `GET` dưới `3,0 s`) |
| **V3.3 (Thermal)** | Sàn nghỉ hậu tải `min_idle_gap = 0,50 s` & xoay vòng TCP mỗi `400` req (`0 ms` overhead) | ✅ Đã triển khai | `RateLimiter` & `HostClient` trong `http.py`; bỏ phạt oan `60 s` sau các bài `2xx` thành công |
| **V3.4 (Extract-Turbo E1–E5)** | `_FastGzipStream` `512 KiB` (`zlib` C-level CRC), cache thư mục `created_dirs`, pool `extract_workers = 2` + `paper_pending_versions`, trần RAM queue `192 MiB` & fast-path `_install_version` | ✅ Đã triển khai | `_FastGzipStream` & `_write_tar_members_from_stream` trong `source.py`, pool `extract-worker` & `_enqueue_extract_job` trong `pipeline.py` |
| **V3.5 (Anti-Cascade & Smart Backup)** | Retry Range $\ge 3,0\text{ s}$ (`3,0→4,5→6,0 s`, trần 4 lượt + `recycle_session`), bỏ phạt oan sau bài `ok`, Backup nền song song `skip_if_unchanged=True` | ✅ Đã triển khai | `_stream_retry_backoff_seconds` trong `source.py`, `_handle_auto_resume_cooldown` trong `pipeline.py`, `skip_if_unchanged` trong `checkpoint.py` |
| **V3.6 (Persistent Resume & Short Cooldown)** | Bảo toàn byte tải dở `.resume-{id}-v{version}.part` qua `429`/defer, đệm `+3,0 s` khi đứt stream $\ge 4\text{ MiB}$, Cooldown tuyến tính ngắn `30→60→90→120 s` (giảm bậc sau 2 bài `ok`, log mỗi 60 s), vét deferred sau 5 bài & gom lô S2 `12,0 s` | ✅ Đã triển khai | `resume_partial_path` & `download_source` trong `source.py`, `_compute_arxiv_cooldown`, `_drain_chunk_deferred` & `_s2_worker` trong `pipeline.py` |
| **V3.7 (3.15s Sweet-Spot, Backup Throttle & S2 Anti-Requeue)** | Nhịp mặc định `3,15 s` (`min_idle_gap = 1,50 s`, `recycle_every = 120` req), nghỉ trọn `>= 3,15 s` `End-to-Start` sau gói nặng $\ge 4\text{ MiB}$, điều tiết backup trước cooldown ($\ge +100$ `final_pairs` & $\ge 15$ phút), bỏ `_requeue_s2_jobs` tránh lặp vô tận, gom lô S2 `45,0 s` & vét giữa chunk sau `25` bài sạch (giữ `fast_defer`) | ✅ Đã triển khai | `Config` trong `config.py`, `_download_version`, `_handle_auto_resume_cooldown`, `_handle_s2_batch`, `_drain_chunk_deferred` trong `pipeline.py` |
| **V3.8 (5.0s Fast-Recovery, 0..0.30s Jitter & 300-ID Cooldown)** | Trần thích ứng `5,0 s` khi gặp `429` (`+0,50 s`) nhưng giảm nhanh `-0,75 s` sau mỗi `2` lượt `GET 200`, Jitter ngẫu nhiên `3,15 s + random(0..0,30 s)` & giải nhiệt chủ động `60 s` sau mỗi `300` ID thực tải (dùng chung `_handle_auto_resume_cooldown`) | ✅ Đã triển khai | `RateLimiter` & `make_arxiv_client` trong `http.py`, `Config` trong `config.py`, `_handle_auto_resume_cooldown` & `_run_chunks` trong `pipeline.py` |

---

## 10. Lệnh vận hành và Tham chiếu nhanh

```bash
# 1. Kiểm tra kế hoạch mở rộng ID (không gọi mạng, không ghi file)
python src/run_crawl.py --ranges src/id_ranges.csv --dry-run

# 2. Chạy thử nghiệm (Pilot 30 ID đầu, kèm tự động vượt 429)
python src/run_crawl.py --ranges src/id_ranges.csv --limit 30 --data-dir data --auto-resume-rate-limit

# 3. Chạy thu thập thực tế toàn bộ dải (resume tự động từ backup cũ, tự động chờ cooldown khi gặp 429)
# Thiết lập biến môi trường (hoặc đặt trong file .env):
#   CRAWLER_CONTACT="email-nhom@student.hcmus.edu.vn"
#   S2_API_KEY="your-s2-api-key"
python src/run_crawl.py \
  --ranges src/id_ranges.csv \
  --data-dir data \
  --backup-dir /path/to/drive/arxiv-data.tar.gz \
  --backup-every-ids 1000 \
  --auto-resume-rate-limit

# 4. Chế độ chỉ cào bù trích dẫn Semantic Scholar (bỏ qua 100% mạng arXiv)
python src/run_crawl.py --ranges src/id_ranges.csv --data-dir data --s2-only

# 5. Kiểm tra toàn vẹn corpus và xuất thống kê cho Jupyter Book (hỗ trợ cả thư mục data, backup cũ .tar.gz và gộp nhiều file session stats)
python src/validate_corpus.py --data-dir data
python src/export_data.py --data-dir data
python src/export_data.py --data-dir /path/to/arxiv-data.tar.gz --out-dir data/stats
python src/export_data.py --sessions-only --stats data/logs/metrics.json --final-output-dir data --output data/stats/benchmark_sessions.json

# 6. Kiểm tra chất lượng mã nguồn (bắt buộc trước khi commit/PR)
ruff check . && ruff format . && pytest
```

**Mã thoát (Exit codes) của `src/run_crawl.py`:**
- `0`: Hoàn tất toàn bộ dải ID.
- `2`: Lỗi cấu hình (thiếu `CRAWLER_CONTACT` hoặc file `--ranges` không hợp lệ).
- `3`: `StopCrawl` — arXiv trả về HTTP `403`, hoặc vượt số lần thử lại `429` khi không bật `--auto-resume-rate-limit` (hoặc vẫn bị chặn sau khi đã chạy lượt deferred).
- `130`: Người dùng ngắt bằng `Ctrl+C` (`KeyboardInterrupt` — dữ liệu và metrics đã được lưu an toàn).

---

## 11. Giới hạn đã biết & Lưu ý khi viết Báo cáo

1. **Điểm nhấn kỹ thuật trong báo cáo Jupyter Book:**
   - **Tuân thủ tuyệt đối chuẩn lịch sự (`Start-to-Start >= 3,0 s`, 1 kết nối tới `export.arxiv.org`)** nhưng vẫn kéo thời gian thực tế 5.000 ID về sát trần vật lý lý thuyết (`~6,85 – 6,92 giờ` khi mạng sạch và `~6,92 – 7,15 giờ` giờ cao điểm) nhờ kiến trúc **Producer–Consumer đa luồng (`extract_workers = 2`)**, đệm RAM Zero-Copy `96 MiB` (`memoryview`, trần hàng đợi `192 MiB`), giải nén 1-pass `512 KiB` (`_FastGzipStream`) giữ `.tex`/`.bib`/`.bbl`, nối luồng đứt bằng `HTTP 206 Range` lịch sự (`3,0 → 4,5 → 6,0 s`, tối đa 4 lượt kèm đổi socket), **Defer tức thì + Cooldown leo thang `60 → 120 → 240 → 360 → 600 s`** và **Backup nền song song có kiểm tra thay đổi (`skip_if_unchanged=True`)**.
   - **Thống kê đa phiên chuẩn xác (`benchmark_summary` & `cross_version_dedup`):** Bóc tách tường minh thời gian chạy thực (`active_wall_seconds`) khỏi thời gian nghỉ giữa các phiên Colab (`idle_gap_seconds`) và thời gian chờ phạt (`cooldown_retry_seconds`), đồng thời định lượng tỷ lệ trùng lặp mã nguồn giữa các phiên bản bằng `sha256` làm cầu nối sang Milestone 2.
2. **Môi trường Windows:** Trình diệt virus (Windows Defender) đôi khi quét file trong `.vK.part` lúc `os.replace`; `pipeline.py` đã bọc bắt lỗi `OSError`, khôi phục bản cũ và đánh dấu `failed` để lần chạy sau tự retry (rủi ro R8).
3. **Đóng băng nộp bài (R6):** Trước khi nộp link repo mã Milestone 1, cần dọn các file quản trị nội bộ (`STATUS.md`, `AGENTS.md`, `CONTRIBUTING.md`, `docs/`, `project requirements/`) theo đúng quy định đề bài (chỉ giữ `.py`, `.ipynb`, `requirements.txt`, `README.md`).

---

## 12. Phần C của M2: gom nguồn và dựng cây (08/11)

`parsing.pipeline.process_paper` đã có điểm gọi hai module C thật:

```text
Corpus.tex_files(id, version)
  → gathering.gather_compilation_unit(id, version, raw_files)
  → CompilationUnit
  → hierarchy.build_raw_hierarchy(unit, CleanerHooks của B)
  → RawNode[] + bibliography bổ sung → các stage tham khảo/dedup của D
```

- Gathering chỉ đọc từ điển bytes đầu vào: giải mã, khử comment có bảo vệ literal,
  chọn root đa tín hiệu, DFS include có chặn chu trình/độ sâu 15 và đếm file duy nhất.
  Điểm include chỉ cộng khi đích tìm được trong mapping từ thư mục root ứng viên;
  không thưởng include thiếu của file mẫu. Sửa root sai ở `2408.11049` v4/v5:
  từ template dùng 1/14 TeX sang `main.tex` dùng 8/14 TeX và nhận bibliography.
  Không tải lại mạng, không thực thi TeX, không sửa corpus. Lỗi include được log
  với ID/version/path; có cảnh báo đồng nghĩa kết quả có thể chỉ là một phần.
- Hierarchy dùng stack cấp bậc, có list/item lồng nhau và lá sentence/formula/figure.
  Gọi `CleanerHooks` được inject, không import trực tiếp module B. Giữ Abstract,
  Acknowledgements và Appendix; tách References khỏi mọi nội dung nút, kể cả list/item.
  Phiên bản không có root trả `([], [])`; lỗi hook được đưa lên pipeline.
- Môi trường thiếu dấu đóng được phục hồi tại đề mục tiếp theo; cấu trúc lồng quá
  64 mức báo lỗi rõ ràng, không âm thầm tạo cây sai. Đây là parser giới hạn theo
  đặc tả, không mô phỏng toàn bộ lệnh/điều kiện/package TeX.
- `.bbl` liên quan được xử lý khi có trong `raw_files`, nhưng `Corpus.tex_files()`
  hiện vẫn lọc `.tex/.bib`: cần A/D nối đường truyền `.bbl` trước tích hợp đầy đủ.
  Schema và module các owner khác được giữ nguyên trong phần C.
- Unit test C và test tích hợp `Stages` dùng nguồn tổng hợp, B/D giả: **98 test C**,
  toàn suite **359 passed**. Không gọi mạng trong unit test.
- Corpus leader cung cấp ở `arxiv-data/`: metadata **1.000 bài / 1.678 phiên bản**;
  349 tệp của mẫu 10 bài khớp kích thước/SHA-256. Đã gọi trực tiếp hai hàm C trên
  **26 phiên bản + 3 đối chứng** với hook truyền nguyên văn: cây/marker References đạt,
  dùng **265/324 TeX**. Sáu phiên bản mẫu thiếu bibliography trong subset; không có
  `.bbl` để thử đường dữ liệu thật. Chưa đối chiếu gói gốc hoặc validate toàn subset.
- M2-C còn 🟡: chưa chứng minh clean/math/split B, tham khảo/dedup/export D, validator E,
  pipeline end-to-end hoặc benchmark Colab. Bản thảo giao E:
  `docs/reports/m2/01_parsing_hierarchy.md`; bằng chứng và đầu việc tích hợp ở
  `docs/reports/m2/02_c_real_sample_check.md` + `c_real_sample_check.json`.

*Tài liệu được rà soát và đồng bộ với mã nguồn `arxiv-pipeline` ngày **08/11/2026**.*
