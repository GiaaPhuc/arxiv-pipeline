# Hướng dẫn crawl arXiv trên Google Colab

Tài liệu này dành cho người mới: chạy crawler Milestone 1 trên **Colab chỉ CPU**, không cần cài Python trên máy cá nhân.

Notebook có sẵn trong repo: [`src/colab_run.ipynb`](../src/colab_run.ipynb). Bạn có thể mở notebook đó trên Colab hoặc làm theo các bước dưới đây.

---

## Trước khi bắt đầu

| Việc cần có | Ghi chú |
| --- | --- |
| Tài khoản Google | Để dùng Colab và Google Drive (sao lưu) |
| **Khóa Semantic Scholar** | Xin tại trang API của Semantic Scholar; **không** dán key vào notebook hay chat |
| **Email hoặc URL liên hệ** | Dùng cho User-Agent khi gọi arXiv (`CRAWLER_CONTACT`) |
| Hiểu quy tắc nhóm | Cả nhóm crawl **25.000 ID trên một máy** (file `src/id_ranges.csv`). Không bật crawl full trên nhiều Colab cùng lúc |

**Thời gian:** crawl đủ 25.000 bài có thể mất **nhiều ngày** (arXiv bắt chờ ≥ 3 giây giữa các request). Colab free có thể **ngắt session** — pipeline **resume** được nếu bạn giữ thư mục `data/` và sao lưu định kỳ.

---

## Crawl khi không treo máy liên tục

Crawler **được thiết kế cho kiểu này**: bạn không cần một session 2–3 ngày liên tục. Mỗi lần bật máy chỉ chạy **một đoạn**, tắt đi, hôm sau chạy tiếp — **không tải lại** bài đã xong.

### Ý tưởng

| Thành phần | Vai trò |
| --- | --- |
| Thư mục `data/` | Chứa bài đã crawl trên đĩa local (`/content/data`) + `manifest.sqlite` (ai xong, ai `failed`, ai còn `pending`) |
| Gói sao lưu Drive | Một file nén duy nhất `MyDrive/arxiv-data.tar.gz` (hoặc `arxiv-data.tar`), giải nén/tải lên trong vài chục giây thay vì copy hàng chục nghìn file nhỏ |
| Cùng một lệnh mỗi lần | `python run_crawl.py --ranges id_ranges.csv` (cùng `--data-dir` / `CRAWLER_DATA_DIR`) |
| Sao lưu tự động | Pipeline tự động nén `data/` lên Drive mỗi **300 ID mới** (~35 phút) và tự sao lưu lần cuối khi crawler thoát |

Lần chạy sau: manifest **bỏ qua** cặp (bài, phiên bản) đã ở trạng thái cuối; **`failed`** và việc **S2 chưa xong** được **xếp lại hàng đợi**. Dừng bằng **Interrupt** / Ctrl+C vẫn an toàn — dữ liệu đã ghi vẫn giữ, chạy lại lệnh để tiếp.

### Hai kịch bản dừng / chạy tiếp (Colab)

#### Kịch bản A: Dừng tạm nhưng runtime vẫn còn sống (Interrupt / hết lệnh / lỗi exit 3)
*Dữ liệu trong `/content/data` vẫn còn nguyên vẹn trên máy ảo.*
1. `run_crawl.py` đã tự động sao lưu lần cuối vào `arxiv-data.tar.gz` khi thoát; nếu muốn chắc chắn, chạy thêm hàm `backup_now()`.
2. Để crawl tiếp: chạy lại cell **Crawl** (không cần chạy lại Restore; nếu lỡ chạy `restore_now()`, hàm sẽ tự phát hiện dữ liệu local đã đủ và bỏ qua).
3. Nếu sắp đóng tab / tắt máy: chạy `backup_now(unmount=True)` để Colab đẩy hết dữ liệu còn trong bộ đệm lên Google Drive trước khi ngắt.

#### Kịch bản B: Bị disconnect / mất runtime (máy ảo bị recycle)
*Dữ liệu local `/content/data` đã mất.*
1. Chạy lại từ đầu: **Setup** (mount Drive, cấu hình) → **Restore** (`restore_now()`) → **Crawl**.
2. Dữ liệu sẽ được khôi phục từ bản `arxiv-data.tar.gz` gần nhất trên Google Drive. Bạn chỉ mất tối đa phần crawl sau lần auto-backup gần nhất (~35 phút).

---

## Quy trình trên laptop / PC (Windows / Linux)

- Đặt `CRAWLER_DATA_DIR` (hoặc `--data-dir`) vào ổ cố định, ví dụ `D:\arxiv-data\data`.
- Khi rảnh: mở terminal, `activate` venv, chạy cùng lệnh crawl; khi cần ngủ / mang máy đi: **Ctrl+C** một lần, đợi process thoát (mã **130** là bình thường).
- **Không** chạy crawl song song trên cùng thư mục `data/` (kể cả Colab + máy nhà cùng lúc).
- Sao lưu định kỳ cả thư mục `data/` (nén zip/tar hoặc copy) lên Drive/USB — nếu ổ hỏng vẫn restore được.

### Một máy chính, nhiều ngày (quyết định D8)

Cả nhóm vẫn chỉ **một** corpus trên **một** máy đang crawl. Người khác **không** bật full crawl trên Colab riêng; chỉ xem tiến độ (log, manifest) hoặc chạy lại `failed` theo phân công khi máy chính sẵn sàng.

### Theo dõi tiến độ nhanh

- Log: `data/logs/crawl.log` (dòng `progress …/25000 ids | …`).
- File `data/logs/metrics.json`: thời gian tích lũy qua **nhiều lần resume** (phù hợp ghi trong báo cáo nếu nói rõ “crawl nhiều phiên”).

### Việc không nên làm

- Ghi `data/` **trực tiếp** lên Google Drive FUSE rồi chạy crawl — SQLite dễ lỗi hoặc hỏng cơ sở dữ liệu; luôn crawl trên `/content/data` (đĩa cục bộ) rồi nén lưu ra Drive.
- Dùng `rsync` từng file nhỏ lên Drive — Google Drive FUSE rất chậm với hàng chục nghìn file nhỏ (từng mất >11 giờ cho 7.000 ID). Luôn dùng `restore_now()` / `backup_now()`.
- Giảm delay / mở nhiều tab crawl để “kịp deadline” — dễ bị dính 403/429 từ arXiv, cả pipeline sẽ dừng (mã **3**).
- Copy nửa chừng `manifest.sqlite` không khớp với `papers/` — hệ thống có cơ chế chống shrink và chống rỗng, không tự ý can thiệp thủ công vào file sqlite khi chưa hiểu rõ.

---

## Hướng dẫn từng bước trên Colab

### Bước 1 — Mở Colab và lấy mã

1. Vào [Google Colab](https://colab.research.google.com/).
2. **File → Upload notebook** → chọn `src/colab_run.ipynb` từ repo.
3. Trong notebook, chạy ô clone/pull repo:

```bash
import os

if not os.path.isdir("/content/arxiv-pipeline"):
    !git clone https://github.com/GiaaPhuc/arxiv-pipeline.git
else:
    !git -C /content/arxiv-pipeline pull --ff-only
%cd /content/arxiv-pipeline/src
!pip install -q -r requirements.txt
```

4. **Runtime → Change runtime type → Hardware accelerator: None** (chỉ CPU, đúng với cách chấm điểm).

---

### Bước 2 — Cấu hình bí mật (Secrets) & Mount Drive

1. Trên Colab: chọn biểu tượng **🔑 Secrets** ở thanh bên trái.
2. Thêm secret tên **`S2_API_KEY`**, giá trị = khóa Semantic Scholar của bạn.
3. Chạy ô Setup trong notebook:
   - Mount Google Drive an toàn (tự xử lý nếu session cũ bị treo mount).
   - Đặt biến môi trường:
     ```python
     os.environ["S2_API_KEY"] = userdata.get("S2_API_KEY")
     os.environ["CRAWLER_CONTACT"] = "email-cua-ban@example.com"
     os.environ["CRAWLER_DATA_DIR"] = "/content/data"
     os.environ["CRAWLER_BACKUP_DIR"] = "/content/drive/MyDrive/arxiv-data.tar.gz"
     os.environ["CRAWLER_BACKUP_EVERY_IDS"] = "300"  # tự nén backup mỗi 300 ID mới (~35 phút)
     os.environ["CRAWLER_ARXIV_MIN_INTERVAL"] = "3.5"
     ```
   - Định nghĩa các hàm `backup_now()` và `restore_now()`.

---

### Bước 3 — Kiểm tra kế hoạch (tùy chọn)

```bash
!python run_crawl.py --ranges id_ranges.csv --dry-run
```

- **Mục đích:** Kiểm tra file `id_ranges.csv` và các cấu hình môi trường mà **không gọi mạng** và **không ghi file**.
- **Lưu ý:** Chỉ cần chạy ở lần đầu để kiểm tra; ở các lần resume sau bạn có thể bỏ qua bước này.

---

### Bước 4 — Khôi phục dữ liệu (Restore)

Chạy cell **Restore** (`restore_now()`) trước khi bắt đầu crawl:

```python
restore_now()
```

Hàm này tự động xử lý thông minh:
- **Nếu đã có `arxiv-data.tar.gz` trên Drive:** Giải nén vào `/content/data` chỉ trong vài chục giây.
- **Nếu chỉ có thư mục `arxiv-data/` cũ:** Tự động copy song song 16 luồng (có in tiến độ mỗi 250 bài), sau đó đóng gói ngay thành `arxiv-data.tar.gz` để các lần sau chạy cực nhanh.
- **Nếu local `/content/data` đã có đủ dữ liệu:** Tự động bỏ qua để tránh ghi đè nhầm tiến độ đang chạy.
- **Nếu là lần đầu chạy (Drive chưa có gì):** Báo `No backup on Drive (first run)` và tạo thư mục trống để crawl.

---

### Bước 5 — Chạy crawl

**Chạy thử (pilot 30 ID đầu tiên):**
```bash
!python run_crawl.py --ranges id_ranges.csv --limit 30
```

**Crawl chính thức toàn bộ dải:**
Bỏ tham số `--limit` để crawler chạy hết danh sách:
```bash
!python run_crawl.py --ranges id_ranges.csv
```

- **Khi đang crawl:** Cứ mỗi **300 ID mới**, crawler tự động snapshot SQLite WAL và đóng gói đè an toàn vào `arxiv-data.tar.gz` trên Drive.
- **Dừng tay an toàn:** Bấm nút **Runtime → Interrupt execution** (tương đương Ctrl+C). Crawler sẽ hoàn tất bài hiện tại, ghi log và tự động chạy backup lần cuối trước khi thoát.

---

### Bước 6 — Sao lưu tay trước khi tắt Colab (Backup)

Khi bạn muốn tắt máy hoặc đóng tab Colab:
1. Interrupt cell crawl.
2. Chạy cell Backup:
   ```python
   backup_now(unmount=True)
   ```
   Lệnh này sẽ nén dữ liệu, copy lên Drive, đồng thời gọi `drive.flush_and_unmount()` để ép Colab đẩy toàn bộ cache từ bộ nhớ ảo lên Google Drive đám mây an toàn 100%.

---

### Bước 7 — Sau khi crawl xong (hoặc kiểm tra pilot)

Khi toàn bộ 25.000 ID (hoặc đợt pilot) đã chạy xong không còn lỗi:
```bash
!python validate_corpus.py --data-dir /content/data
!python export_data.py --data-dir /content/data
```

- `validate_corpus.py` thoát mã **0** = PASS (cấu trúc thư mục, phiên bản, hash file .tex/.bib và tham chiếu đều hợp lệ).
- `export_data.py` xuất bảng dữ liệu và thống kê vào `data/stats/` (Parquet + CSV) phục vụ làm báo cáo Jupyter Book.

---

## Dữ liệu nằm ở đâu?

Trên đĩa máy ảo (`/content/data`):
```
data/
  manifest.sqlite      # SQLite WAL quản lý trạng thái từng bài/phiên bản
  papers/<arxiv_id>/   # metadata (paper.json), từng phiên bản v1..vN, tex/bib, references
  logs/                # crawl.log, metrics.json
  stats/               # sau khi chạy export_data.py
```

Trên Google Drive cá nhân (`MyDrive/`):
- `arxiv-data.tar.gz`: Gói nén toàn bộ workspace `data/` (đã loại bỏ rác `.tmp`).
- `arxiv-data.tar.gz.meta.json`: File chứa metadata số bài và dung lượng để chống ghi đè lùi tiến độ.
- `arxiv-data/` (thư mục cũ nếu có): Có thể xóa thủ công sau khi đã kiểm tra file `arxiv-data.tar.gz` hoạt động tốt.

---

## Khi gặp lỗi

| Triệu chứng | Việc nên làm |
| --- | --- |
| Thoát mã **2** | Thiếu `CRAWLER_CONTACT` hoặc lỗi đọc `id_ranges.csv` — sửa lại Bước 2 / 3 |
| Thoát mã **3** | arXiv **403**, hoặc **429** trên API discovery hoặc `/src/` sau khi đã chờ `Retry-After` tối đa 4 lần — **dừng lại**, chờ **30–60 phút**, rồi chạy lại lệnh crawl (resume). **Không** cố spam hay giảm delay |
| `Mountpoint must not already contain files` | Lỗi do Drive mount bị treo từ phiên trước. Bấm **Runtime → Restart session**, rồi chạy lại từ ô Setup |
| `validate_corpus` / `export_data` lỗi | Do corpus **chưa crawl xong** (dừng giữa chừng, thiếu S2). Chỉ validate khi đã crawl hoàn tất |
| S2 chậm / 429 | Cần kiểm tra lại `S2_API_KEY` trong Colab Secrets. Pipeline có cơ chế adaptive backoff tự động |
| Session Colab bị chết giữa chừng | Mở lại Colab, chạy Setup → `restore_now()` → chạy lại lệnh crawl ở Bước 5 |

---

## Quy tắc lịch sự (bắt buộc)

- arXiv **chỉ** qua `export.arxiv.org`, **≥ 3,5 giây** trên Colab giữa các request, **một** kết nối duy nhất.
- Semantic Scholar **≥ 1,1 giây** giữa các request (limiter tự động thích ứng khi gặp 429).
- User-Agent luôn có thông tin liên hệ từ `CRAWLER_CONTACT`.

---

## Tóm tắt quy trình chuẩn

### 1. Chủ động dừng xong thời gian sau crawl tiếp (tắt máy / đóng tab)
- **Khi muốn dừng:** Bấm **Runtime → Interrupt execution** (dừng crawl) → chạy cell Backup: `backup_now(unmount=True)`. Đợi thông báo `backup done` và `Drive flushed` là có thể yên tâm đóng tab, tắt máy.
- **Khi muốn crawl tiếp (phiên làm việc sau):** Mở lại notebook Colab → chạy lần lượt: **Setup** → **Restore** (`restore_now()`) → **Crawl** (cùng đúng một lệnh crawl, manifest sẽ tự động bỏ qua toàn bộ bài đã xong).

### 2. Bị disconnect bất ngờ giữa chừng (mất runtime / Colab tự tắt)
- Dữ liệu đã được pipeline tự động nén lưu định kỳ mỗi 300 ID mới (~35 phút) vào `arxiv-data.tar.gz` trên Drive, bạn chỉ mất tối đa bài của đợt crawl dở dang gần nhất.
- **Cách xử lý:** Mở lại Colab, chạy lại từ đầu: **Setup** → **Restore** (`restore_now()`) → **Crawl**. Dữ liệu sẽ được giải nén nguyên vẹn về local và tiếp tục crawl các ID còn lại.

### 3. Dừng tạm nhưng runtime vẫn còn sống (chưa tắt tab, chưa disconnect)
- Dữ liệu local tại `/content/data` vẫn còn nguyên vẹn.
- Bạn **không cần** chạy lại Restore (nếu lỡ bấm `restore_now()`, code cũng tự phát hiện dữ liệu local đã đủ và bỏ qua); chỉ cần chạy lại cell **Crawl** để tiếp tục.
