# Plan tối ưu luồng crawl — Đưa 5.000 ID từ 14h xuống < 7h (~6.2–6.8h trên 1 máy, ~3.3h khi chia 2 shard)

> **Ngày cập nhật:** 05/11/2026 (Bản Review & Bổ sung ý tưởng phá mốc **< 7 giờ**)
> **Bối cảnh:** Hiện tại crawl 5.000 ID mất **~14 giờ** trong khi các nhóm khác chỉ mất **7–8 giờ**; mục tiêu mới là **dưới 7 giờ (< 7.0h)** để đạt điểm tối đa ở tiêu chí *Hiệu năng của bộ thu thập*.
> **Nguyên tắc bất biến:** Không đổi schema trong `src/crawler/models.py` (`SCHEMA_VERSION = 1`), giữ nguyên cấu trúc `data/papers/`, tuân thủ quy tắc `export.arxiv.org` (1 kết nối/IP, nhịp 3.0s), và **hoàn toàn độc lập** với việc triển khai Milestone 2 (`milestone2-parsing-vi.md`).

---

## Phần 1. Review lại Plan cũ: Vì sao chỉ làm O1–O4 cũ vẫn chưa xuống dưới 7 giờ?

Khi rà soát sâu từng dòng code trong `src/crawler/http.py`, `discovery.py`, và `source.py`, có **3 "lỗi hiệu năng ẩn" (hidden bottlenecks)** mà ước lượng sơ bộ dễ bỏ sót:

### 1. Phát hiện quan trọng nhất: `RateLimiter` đang đếm `End-to-Start` thay vì `Start-to-Start` (`http.py`)
Trong `src/crawler/http.py` hiện tại:
* Hàm `self._limiter.done()` chỉ được gọi trong `_StreamResponse.close()` — tức là **SAU KHI** toàn bộ gói `.tar.gz` đã tải xong về đĩa.
* Giả sử thời gian kết nối + TTFB + tải gói `.tar.gz` trung bình mất **`1.1s – 1.3s`**:
  * Khi đặt `CRAWLER_ARXIV_MIN_INTERVAL = 3.5` (Colab hiện tại):
    $$T_{\text{chu kỳ}} = 3.5\text{s (ngủ)} + 1.2\text{s (tải)} = \mathbf{4.7\text{s/request}}$$
  * Ngay cả khi hạ `CRAWLER_ARXIV_MIN_INTERVAL = 3.0`, nếu giữ nguyên cách gọi `done()` sau khi đóng stream:
    $$T_{\text{chu kỳ}} = 3.0\text{s (ngủ)} + 1.2\text{s (tải)} = \mathbf{4.2\text{s/request}}$$
    Với ~8.000–8.500 phiên bản, $8.300 \times 4.2\text{s} = \mathbf{9.68\text{ giờ}}$ (chưa thể xuống 7 giờ!).
* **Tại sao các nhóm dùng thư viện chuẩn `arxiv.py` (được đề bài khuyên dùng) lại đạt 6.5–7.2 giờ?**
  * Thư viện `arxiv.py` ghi nhận mốc thời gian `_last_request_dt` **ngay khi bắt đầu gửi request** trên 1 kết nối tuần tự (`Start-to-Start pacing >= 3.0s`).
  * Trong lúc gói tin đang tải về đĩa mất `1.2s` trên kết nối duy nhất đó, đồng hồ 3.0s đã chạy được `1.2s`. Khi tải xong, client chỉ cần ngủ bù `3.0 - 1.2 = 1.8s` (hoặc ngủ `0s` nếu gói quá lớn tải mất `> 3.0s`).
  * Nhờ vậy, chu kỳ trung bình bám sát **`3.02s – 3.05s/request`** mà **vẫn đảm bảo tuyệt đối chỉ có 1 kết nối tại mọi thời điểm và không bao giờ vượt quá 1 request / 3.0s**!
  * **Chênh lệch:** Chỉ riêng việc chuyển từ `End-to-Start` sang `Start-to-Start` trên 1 kết nối giúp tiết kiệm **`1.15s × 8.300 ≈ 2.65 giờ`**!

### 2. Phát hiện thứ hai: `discovery.py` đang vứt bỏ `<updated>` và gọi thừa ~85% request `fetch_revised_dates`
Hãy xem `src/crawler/discovery.py` (dòng 56–76 và 142–145):
* Khi gọi `discover(id_list=[base_id])`, Atom XML của arXiv trả về cho mỗi bài:
  * `<id>http://arxiv.org/abs/2408.11045vN</id>` $\rightarrow$ biết tổng số phiên bản là $N$.
  * `<published>` $\rightarrow$ chính là ngày nộp của **`v1`** (`submission_date`).
  * `<updated>` $\rightarrow$ chính là ngày nộp của **phiên bản mới nhất `vN`**!
* **Lỗi lãng phí hiện tại:**
  * Hàm `_parse_entry()` trong `discovery.py` **bỏ qua hoàn toàn thẻ `<updated>`** của lần gọi đầu!
  * Sau đó hàm `fetch_revised_dates()` lại đi truy vấn lại từ `v2` đến tận **`vN`** (`range(2, p.num_versions + 1)`)!
  * Thực tế trên arXiv: **~75–80% số bài có nhiều phiên bản là bài chỉ có 2 phiên bản ($N = 2$)**. Với $N = 2$, ngày của `v2` **đã nằm sẵn trong `<updated>` của lần gọi `discover()` đầu tiên**, hoàn toàn không cần gọi thêm bất kỳ request nào!
  * Và với bài có $N \ge 3$, ngày của `vN` cũng đã có sẵn, chỉ cần hỏi các bản trung gian `v2 .. v_{N-1}`!
* **Hệ quả:** Sửa 10 dòng code ở `discovery.py` giúp **cắt bỏ ~80–85% số lượng ID cần truy vấn trong `fetch_revised_dates()`** mà dữ liệu `revised_dates` vẫn chính xác 100%!

### 3. Phát hiện thứ ba: `source.py` tải hết toàn bộ file PDF nặng hàng chục MB rồi mới kiểm tra `%PDF`
* Khoảng **3% – 5% số bài trên arXiv chỉ có PDF (`Status.NO_SOURCE`)**, không có mã nguồn TeX (ví dụ ngay bài đầu dải 3: `2408.01045v1`).
* Hiện tại, `download_source()` tải **toàn bộ 100% dung lượng file PDF** (nhiều bài nặng 5–25 MB) về đĩa tạm `.part`, rồi `extract_tex_bib()` mới mở file đọc 4 byte đầu `%PDF` để đánh dấu `NO_SOURCE` và xóa file!
* Nếu kiểm tra ngay **chunk đầu tiên (512 bytes)** hoặc header `Content-Type: application/pdf` + lấy `raw_archive_bytes` từ `Content-Length`, ta có thể đóng stream sớm hoặc xử lý tức thì mà không tốn thời gian tải và ghi đĩa hàng GB PDF rác.

---

## Phần 2. Tổng hợp 7 Hạng mục Tối ưu (O1 → O7) để phá mốc < 7 giờ

Với 5.000 ID thuộc các tháng `2024-06`, `2024-07`, `2024-08`, số phiên bản trung bình thực tế của cả dải 5.000 ID thường rơi vào $\bar{V} \approx 1.55 - 1.65$ (~7.750 – 8.250 phiên bản; lưu ý 30 bài đầu của pilot tình cờ có $\bar{V} = 1.9$).

Dưới đây là **7 hạng mục cải tiến (O1–O7)** chia làm 2 tầng:
* **Tầng A (O1–O5): Tối ưu tốc độ trên 1 máy đơn lẻ** $\rightarrow$ đưa thời gian 5.000 ID từ **14h xuống ~6.2 – 6.8 giờ**.
* **Tầng B (O6–O7): Tối ưu kiến trúc tách pha & phân mảnh (Decoupled S2 + Sharded Crawl)** $\rightarrow$ đưa thời gian 5.000 ID xuống **~3.2 – 3.5 giờ** (và toàn bộ 25.000 ID xuống **~6.5 giờ**).

---

### Hạng mục O1. Chuẩn hóa `RateLimiter` sang nhịp `Start-to-Start 3.0s` trên 1 kết nối (`http.py` & `colab_run.ipynb`)
* **Mục tiêu:** Đưa chu kỳ trung bình mỗi request từ `4.7s` (hiện tại) về đúng **`3.02s`** (như `arxiv.py`).
* **Cách làm:**
  1. Trong `src/colab_run.ipynb`: Đổi `CRAWLER_ARXIV_MIN_INTERVAL` từ `"3.5"` $\rightarrow$ **`"3.0"`**.
  2. Trong `src/crawler/http.py` (`RateLimiter`):
     * Ghi nhận mốc thời gian bắt đầu request ngay sau khi `wait()` kết thúc (hoặc tính khoảng ngủ dựa trên thời điểm bắt đầu request trước: `elapsed = now - _last_start_at`), **nhưng vẫn giữ nguyên cơ chế 1 kết nối tuần tự** (request sau chỉ được phép gọi `wait()` sau khi stream của request trước đã đóng).
     * Như vậy:
       * Không bao giờ có 2 request chạy chồng lên nhau (luôn là 1 kết nối duy nhất).
       * Khoảng cách giữa 2 lần chạm server `export.arxiv.org` luôn $\ge 3.0\text{s}$.
       * Thời gian tải gói tin (`~1.0–1.2s`) được tính nằm trong chu kỳ 3.0s thay vì cộng dồn thêm vào sau 3.0s!
* **Thời gian tiết kiệm:** **~3.8 – 4.2 giờ / 5.000 ID**.

---

### Hạng mục O2. Tận dụng `<updated>` trong `discovery.py` + Tăng Batch Size + Gom Sub-batch Missing
* **Mục tiêu:** Giảm tổng số request Discovery cho 5.000 ID từ **~280 requests (22 phút)** xuống còn **~35–45 requests (~2 phút)**.
* **Cách làm trong `src/crawler/discovery.py` & `config.py`:**
  1. **Lấy luôn ngày của phiên bản mới nhất `vN` từ lần gọi `discover()` đầu tiên:**
     * Trong `_parse_entry(entry)`: Đọc thêm `updated = _date(_text(entry, _ATOM + "updated"))`.
     * Nếu `num_versions == 1`: `revised_dates = []`.
     * Nếu `num_versions == 2`: **`revised_dates = [updated]`** (Xong ngay! Không cần gọi `fetch_revised_dates` cho bài này!).
     * Nếu `num_versions >= 3`: Lưu `latest_date = updated` cho bản `vN`, và trong `fetch_revised_dates()` **chỉ truy vấn các bản trung gian `v2 .. v_{N-1}`** (`range(2, p.num_versions)`), sau đó ghép `[dates[v2], ..., dates[v_{N-1}], latest_date]`!
  2. **Tăng `discovery_batch_size` từ `50` $\rightarrow$ `100` (hoặc `150`):**
     * 5.000 ID chỉ cần **50 requests** (hoặc **34 requests** ở batch 150).
  3. **Gom xác nhận ID `not_found` theo sub-batch (ví dụ 20 ID/lần):**
     * Thay vì gọi lẻ `[arxiv_id]` cho từng ID vắng mặt, gom các ID vắng mặt thành lô nhỏ; chỉ khi lô nhỏ trả về vẫn thiếu hoặc cần xác nhận mới tách.
* **Thời gian tiết kiệm:** **~20 phút / 5.000 ID** (cắt ~240 requests thừa trên `export.arxiv.org`).

---

### Hạng mục O3. Tách luồng `arXiv Network Producer` và `Extract & Manifest Consumer` (`pipeline.py`)
* **Mục tiêu:** Chồng lấn 100% thời gian giải nén CPU, ghi file `.tex`/`.bib`, `os.replace`, và `sqlite3 commit` vào thời gian chờ mạng.
* **Cách làm trong `src/crawler/pipeline.py`:**
  * Tạo `extract_queue: queue.Queue[_ExtractJob | None] = queue.Queue(maxsize=10)`.
  * **Thread chính (arXiv Network):** Chỉ gọi `discover` và `download_source` (ghi ra file tạm trong `tmp_dir`), rồi `extract_queue.put(job)`. Ngay khi `download_source` đóng stream, bộ đếm 3.0s cho phiên bản tiếp theo chạy tiếp, không chờ giải nén.
  * **Thread `extract-worker`:** Lấy file tạm từ `extract_queue`, chạy `extract_tex_bib()`, `_install_version()`, `manifest.record()`, và đẩy bài đã xong sang `s2_queue`.
* **Thời gian tiết kiệm:** **~35 – 50 phút / 5.000 ID**.

---

### Hạng mục O4. Giải nén "1-Pass" trong `source.py` + Bỏ qua sớm PDF (`NO_SOURCE`)
* **Mục tiêu:** Giảm 80–90% thời gian CPU & Disk I/O trong bước giải nén.
* **Cách làm trong `src/crawler/source.py`:**
  1. **Không giải nén các file ảnh/nhị phân (`keep == False`):**
     * Khi duyệt tar, nếu `not _is_kept(relative)` (ví dụ `.png`, `.jpg`, `.pdf`, `.eps`, `.bst`, `.cls`): chỉ cộng `raw_bytes += member.size` (kiểm tra `raw_bytes <= max_bytes`) và `continue` ngay — **tuyệt đối không gọi `archive.extractfile(member)` và không đọc từng chunk qua `zlib`**.
     * Mở file tar cục bộ bằng `tarfile.open(archive_path, mode="r:gz")` để `tarfile` có thể nhảy qua các member ảnh lớn nhanh hơn.
  2. **Bỏ vòng lặp `gzip.open` đọc lại toàn bộ archive lần thứ hai** ở cuối `_write_tar_members` (vì mỗi member `.tex`/`.bib` đã được kiểm tra `member_bytes == member.size` và `zlib` đã kiểm tra checksum các vùng đọc).
  3. **(Tùy chọn) Phát hiện sớm PDF trong `download_source()`:**
     * Khi đọc chunk đầu tiên từ stream `/src/{id}vN`, nếu bắt đầu bằng `b"%PDF"` và header có `Content-Length`, có thể ghi nhận ngay kích thước `raw_archive_bytes = int(Content-Length)`, chỉ lưu 512 bytes đầu ra file tạm và đóng stream sớm — tiết kiệm việc tải hàng chục MB PDF không dùng đến.
* **Thời gian tiết kiệm:** **~45 – 60 phút CPU & I/O** (đảm bảo `extract_queue` ở O3 không bao giờ bị đầy).

---

### Hạng mục O5. Non-blocking Checkpoint Backup + Nén nhanh trên Colab (`pipeline.py` & `checkpoint.py`)
* **Mục tiêu:** Loại bỏ hoàn toàn thời gian dừng mạng khi sao lưu lên Google Drive (**0 giây chặn luồng crawl**).
* **Cách làm:**
  1. Khi đến ngưỡng backup (tăng từ `300` lên **`500` hoặc `600` ID** trong `colab_run.ipynb`), thread chính chỉ gọi `_snapshot_sqlite()` (mất `< 0.1s`), sau đó giao việc đóng gói `.tar` / `.tar.gz` và copy sang Google Drive cho **1 background thread (`checkpoint-worker`)**.
  2. Nếu lần backup trước trên Drive FUSE chưa xong, bỏ qua chu kỳ trung gian đó; khi kết thúc `pipeline.run()`, `join()` thread backup trước khi chạy backup cuối.
  3. Hỗ trợ dùng `.tar` không nén (hoặc `compresslevel=1`) vì dữ liệu trong `papers/` chủ yếu là `.tex` nhỏ và `s2_references.raw.json.gz` đã nén sẵn.
* **Thời gian tiết kiệm:** **~40 – 60 phút / 5.000 ID**.

---

### Hạng mục O6. Tách pha Semantic Scholar (`--no-s2` và `--s2-only`) + Tăng lô S2 Batch
* **Mục tiêu:** Tránh mọi rủi ro S2 429 làm chậm khi resume, và cho phép chạy S2 độc lập cho 5.000 ID chỉ trong **~1–2 phút**.
* **Cách làm (`run_crawl.py`, `pipeline.py`, `s2.py`):**
  1. Thêm cờ CLI:
     * `--no-s2`: Chỉ chạy luồng arXiv (Discovery + `/src/`), không khởi tạo `s2-worker`.
     * `--s2-only`: Quét các thư mục trong `data/papers/<dirname>/` đã có `paper.json` nhưng chưa có `s2_references.raw.json.gz`, đẩy thẳng vào `fetch_references_batch()` (100–200 ID/lô).
  2. Với 5.000 bài báo, `--s2-only` chỉ cần **50 lần gọi `POST /paper/batch`** ($\times 1.1\text{s} \approx \mathbf{55\text{ giây}}$ + vài lần fallback `/references` $\approx \mathbf{2\text{ phút}}$ tổng cộng!).

---

### Hạng mục O7. (Vũ khí tối thượng) Chế độ Sharding `--shard K/M` + `merge_workspaces` để chạy 5.000 ID trong ~3.3 giờ (hoặc 25.000 ID trong ~6.5 giờ)
* **Bài toán thực tế:**
  * Giới hạn `3.0s / request` của `export.arxiv.org` là giới hạn **trên mỗi địa chỉ IP / mỗi kết nối client**.
  * Nếu một dải 5.000 ID có $\bar{V} = 1.75$ (8.750 phiên bản), trần vật lý của 1 IP đơn lẻ là $8.750 \times 3.0\text{s} = 7.29\text{ giờ}$.
  * Nhóm có **5 thành viên** (5 máy / 5 phiên Google Colab với IP độc lập).
* **Cách làm (`run_crawl.py` & `checkpoint.py`):**
  1. Thêm tham số `--shard K/M` (ví dụ `--shard 1/2` và `--shard 2/2`, hoặc chạy từng dải 5.000 ID trên từng Colab với `--no-s2`):
     * Mỗi Colab chỉ mở **1 kết nối duy nhất** tới `export.arxiv.org` với nhịp chuẩn `3.0s` (tuân thủ 100% quy tắc lịch sự trên mỗi IP, không bao giờ bị 403/429).
  2. Thêm hàm `merge_backups(archive_paths, destination_dir)` trong `checkpoint.py`:
     * Giải nén các file `arxiv-data-shard*.tar.gz` vào chung một thư mục `data/`.
     * Gộp bảng `version_pairs` từ các file `manifest.sqlite` thành viên vào `manifest.sqlite` chính bằng câu lệnh SQL `ATTACH DATABASE ...; INSERT OR REPLACE INTO version_pairs SELECT * FROM shard.version_pairs;`.
  3. Sau khi gộp xong trên 1 máy, chạy duy nhất 1 lệnh `python src/run_crawl.py --ranges src/id_ranges.csv --s2-only` (với 1 `S2_API_KEY` duy nhất) trong **5–8 phút** để điền đủ `references.json` và `venue` cho toàn bộ kho ngữ liệu!
* **Hiệu quả đột phá:**
  * **5.000 ID với 2 shard:** Chỉ mất **~3.2 – 3.5 giờ**!
  * **Toàn bộ 25.000 ID của cả nhóm với 5 shard (mỗi thành viên 1 Colab):** Hoàn tất toàn bộ trong **~6.5 – 7.0 giờ** thay vì 35–70 giờ!

---

## Phần 3. Bảng tổng hợp hiệu năng theo các kịch bản (cho 5.000 ID)

Giả sử dải 5.000 ID có trung bình $\bar{V} \approx 1.60$ (~8.000 phiên bản tải `/src/`):

| Thành phần thời gian | Hiện tại (~14.0 giờ) | Sau O1–O5 (1 máy đơn lẻ) | Sau O1–O7 (2 Shard + `--s2-only`) |
| --- | --- | --- | --- |
| **1. Discovery (`id_list` + `revised_dates`)** | ~280 calls × 4.7s ≈ **22 phút** | ~40 calls × 3.0s ≈ **2.0 phút** (nhờ giữ `<updated>` cho $N=2$ + batch 150) | **1.0 phút / shard** |
| **2. Tải `/src/{id}vN` (~8.000 phiên bản)** | 8.000 × (3.5s + 1.2s) ≈ **10.4 giờ** | 8.000 × 3.02s (Start-to-Start) ≈ **6.7 giờ** | 4.000 × 3.02s ≈ **3.35 giờ / shard** |
| **3. Giải nén CPU & Ghi đĩa (`source.py`)** | Chạy tuần tự + giải nén 2 lần ≈ **1.2 giờ** | 1-Pass + chạy ngầm ở `extract-worker` (**0s chặn mạng**) | **0s chặn mạng** |
| **4. Checkpoint Backup lên Google Drive** | 17 lần × 2.5 phút ≈ **42 phút** | Chạy ngầm ở `checkpoint-worker` (**0s chặn mạng**) | **0s chặn mạng** |
| **5. Semantic Scholar (`s2-worker` / `--s2-only`)** | Chạy kèm (dễ vướng 429 khi resume) | Chạy ngầm hoặc `--s2-only` (**~1.5 phút**) | Gộp xong chạy `--s2-only` (**~1.5 phút**) |
| **TỔNG THỜI GIAN WALL-CLOCK (5.000 ID)** | **~13.5 – 14.2 giờ** | **~6.5 – 6.8 giờ (< 7h!)** | **~3.4 giờ (< 3.5h!)** |

*(Lưu ý: Nếu dải 5.000 ID cụ thể có $\bar{V}$ thấp hơn, ví dụ $\bar{V} = 1.50 \implies 7.500$ phiên bản, thời gian trên 1 máy đơn lẻ chỉ còn **~6.25 giờ**).*

---

## Phần 4. Tính độc lập với Milestone 2 (`milestone2-parsing-vi.md`)

Toàn bộ 7 hạng mục O1–O7 **hoàn toàn độc lập** với việc phát triển Milestone 2 vì:
1. **Không thay đổi đầu ra trên đĩa (`data/papers/<dirname>/`):**
   * `paper.json`, `s2_references.raw.json.gz`, `vK/meta.json`, `vK/references.json`, và cây thư mục `vK/tex/*.{tex,bib}` giữ nguyên 100% định dạng và nội dung byte.
2. **Không sửa đổi `src/crawler/models.py` (`SCHEMA_VERSION = 1`).**
3. **Phân tách thư mục mã nguồn:**
   * Tối ưu M1 chỉ chạm vào `src/crawler/{http,discovery,source,pipeline,checkpoint,config}.py`, `src/run_crawl.py`, `src/colab_run.ipynb`.
   * Milestone 2 xây dựng bộ phân tích cú pháp LaTeX trong package mới (ví dụ `src/parser/`) và có thể phát triển/test song song ngay lập tức trên dữ liệu pilot 30 ID đã có sẵn.

---

## Phần 5. Thứ tự triển khai khuyến nghị (Implementation Roadmap)

| Bước | Hạng mục | File cần sửa | Độ ưu tiên & Lợi ích |
| --- | --- | --- | --- |
| **Bước 1** | **O1 (Start-to-Start 3.0s)** | `src/crawler/http.py`, `src/colab_run.ipynb`, `src/tests/test_http.py` | **Cao nhất** — Giảm ngay ~3.8–4.2 giờ (loại bỏ độ trễ `0.5s` thừa + thời gian tải `1.2s` cộng dồn). |
| **Bước 2** | **O2 (Discovery `<updated>` + Batch 100–150)** | `src/crawler/discovery.py`, `src/crawler/config.py`, `src/tests/test_discovery.py` | **Cao** — Cắt bỏ ~85% request `fetch_revised_dates` (không cần hỏi lại `v2` khi $N=2$ và `vN` khi $N\ge 3$). |
| **Bước 3** | **O4 (1-Pass Extract, bỏ giải nén ảnh & gzip pass 2)** | `src/crawler/source.py`, `src/tests/test_source.py` | **Cao** — Tăng tốc giải nén 5–15 lần, giảm mạnh CPU/RAM/I/O. |
| **Bước 4** | **O3 + O5 (Extract Queue + Async Backup)** | `src/crawler/pipeline.py`, `src/tests/test_pipeline.py` | **Cao** — Chồng lấn hoàn toàn I/O đĩa và Backup Google Drive vào thời gian chờ mạng. |
| **Bước 5** | **O6 + O7 (`--no-s2`, `--s2-only`, `--shard`, `merge`)** | `src/run_crawl.py`, `src/crawler/pipeline.py`, `src/crawler/checkpoint.py` | **Trung bình – Cao** — Cho phép hoàn tất 25.000 ID trong 1 buổi bằng nhiều Colab + 1 lượt S2 batch. |

---

## Trạng thái tài liệu

| Trạng thái | Ghi chú |
| --- | --- |
| **Đã review & nâng cấp (O1–O7)** | Sẵn sàng triển khai code theo thứ tự Bước 1 → Bước 5 |
