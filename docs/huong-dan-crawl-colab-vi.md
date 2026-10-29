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

Crawler **được thiết kế cho kiểu này**: bạn không cần một session 2–3 ngày. Mỗi lần bật máy chỉ chạy **một đoạn**, tắt đi, hôm sau chạy tiếp — **không tải lại** bài đã xong.

### Ý tưởng

| Thành phần | Vai trò |
| --- | --- |
| Thư mục `data/` | Chứa bài đã crawl + `manifest.sqlite` (ai xong, ai `failed`, ai còn `pending`) |
| Cùng một lệnh mỗi lần | `python run_crawl.py --ranges id_ranges.csv` (cùng `--data-dir` / `CRAWLER_DATA_DIR`) |
| Sao lưu | Trước khi tắt Colab / đóng laptop: copy `data/` ra nơi an toàn (Drive, ổ cứng ngoài) |

Lần chạy sau: manifest **bỏ qua** cặp (bài, phiên bản) đã ở trạng thái cuối; **`failed`** và việc **S2 chưa xong** được **xếp lại hàng đợi**. Dừng bằng **Interrupt** / Ctrl+C vẫn an toàn — dữ liệu đã ghi vẫn giữ, chạy lại lệnh để tiếp.

### Quy trình mỗi phiên (Colab)

```text
1. Mount Drive → set Secrets / CRAWLER_CONTACT
2. rsync Drive → /content/data          (nếu session mới, trừ lần đầu)
3. python run_crawl.py --ranges id_ranges.csv
4. (Tùy chọn) Interrupt khi sắp hết giờ Colab / cần tắt máy
5. rsync /content/data → Drive
6. Tắt runtime — không cần giữ tab mở qua đêm
```

Lần đầu tiên vẫn nên `--limit 30` để thử; từ lần full crawl trở đi **không** dùng `--limit` (limit chỉ lấy **N ID đầu** file, không phải “thêm N ID mỗi ngày”).

### Quy trình trên laptop / PC (Windows)

- Đặt `CRAWLER_DATA_DIR` (hoặc `--data-dir`) vào ổ cố định, ví dụ `D:\arxiv-data\data`.
- Khi rảnh: mở terminal, `activate` venv, chạy cùng lệnh crawl; khi cần ngủ / mang máy đi: **Ctrl+C** một lần, đợi process thoát (mã **130** là bình thường).
- **Không** chạy crawl song song trên cùng thư mục `data/` (kể cả Colab + máy nhà cùng lúc).
- Sao lưu định kỳ cả thư mục `data/` (zip hoặc copy) lên Drive/USB — nếu ổ hỏng vẫn restore được.

### Một máy chính, nhiều ngày (quyết định D8)

Cả nhóm vẫn chỉ **một** corpus trên **một** máy đang crawl. Người khác **không** bật full crawl trên Colab riêng; chỉ xem tiến độ (log, manifest) hoặc chạy lại `failed` theo phân công khi máy chính sẵn sàng.

### Theo dõi tiến độ nhanh

- Log: `data/logs/crawl.log` (dòng `progress …/25000 ids | …`).
- File `data/logs/metrics.json`: thời gian tích lũy qua **nhiều lần resume** (phù hợp ghi trong báo cáo nếu nói rõ “crawl nhiều phiên”).

### Việc không nên làm

- Ghi `data/` **trực tiếp** lên Google Drive rồi chạy crawl — SQLite dễ lỗi; chỉ **rsync sau** khi crawler dừng.
- Giảm delay / mở nhiều tab crawl để “kịp deadline” — dễ 403/429, cả pipeline dừng (mã **3**).
- Copy nửa chừng `manifest.sqlite` không khớp với `papers/` — luôn backup **cả** thư mục `data/`.

---

## Bước 1 — Mở Colab và lấy mã

1. Vào [Google Colab](https://colab.research.google.com/).
2. **File → Upload notebook** → chọn `src/colab_run.ipynb` từ repo. Notebook đã tách **Restore / Crawl / Backup** — mỗi phiên: restore → crawl; khi dừng: **Interrupt** rồi chạy ô backup. **Hoặc** tạo notebook mới và làm theo các bước dưới.
3. Trong notebook, chạy ô clone repo (hoặc tự chạy):

```bash
!git clone https://github.com/GiaaPhuc/arxiv-pipeline.git
%cd arxiv-pipeline/src
!pip install -q -r requirements.txt
```

4. **Runtime → Change runtime type → Hardware accelerator: None** (chỉ CPU, đúng với cách chấm điểm).

---

## Bước 2 — Cấu hình bí mật (Secrets)

1. Trên Colab: biểu tượng **🔑 Secrets** (hoặc **Notebook settings → Secrets**).
2. Thêm secret tên **`S2_API_KEY`**, giá trị = khóa Semantic Scholar của bạn.
3. Trong notebook, gắn biến môi trường (sửa email thật của bạn):

```python
import os
from google.colab import drive, userdata

# Mount an toàn khi reconnect — copy từ ô tương ứng trong `colab_run.ipynb`
# (không dùng force_remount=True khi /content/drive đã có file).

os.environ["S2_API_KEY"] = userdata.get("S2_API_KEY")
os.environ["CRAWLER_CONTACT"] = "email-cua-ban@example.com"
os.environ["CRAWLER_DATA_DIR"] = "/content/data"
os.environ["CRAWLER_BACKUP_DIR"] = "/content/drive/MyDrive/arxiv-data"
os.environ["CRAWLER_BACKUP_EVERY_IDS"] = "1000"  # tự sao lưu mỗi ~1000 ID (crawl vẫn chạy)
```

**Lưu ý:** Dữ liệu crawl nên nằm ở **`/content/data`** (ổ đĩa tạm Colab), **không** ghi trực tiếp lên Google Drive — SQLite manifest dễ lỗi trên Drive.

---

## Bước 3 — Kiểm tra kế hoạch (không gọi mạng)

```bash
!python run_crawl.py --ranges id_ranges.csv --dry-run
```

Kết quả in số ID (khoảng **25.000**), thư mục data, và có set contact / S2 key hay chưa. Nếu thiếu `CRAWLER_CONTACT`, crawler sẽ **refuse** khi chạy thật.

---

## Bước 4 — Chạy thử (pilot), rồi crawl đủ

**Luôn pilot trước** (30 ID):

```bash
!python run_crawl.py --ranges id_ranges.csv --limit 30
```

Khi ổn, bỏ `--limit` để crawl toàn bộ dải:

```bash
!python run_crawl.py --ranges id_ranges.csv
```

### Resume (chạy lại sau khi ngắt)

- Dùng **đúng cùng một lệnh** như lần trước (cùng `--data-dir` / `CRAWLER_DATA_DIR`).
- ID đã xong được **bỏ qua**; cặp `failed` được **thử lại**.
- **Không** chạy hai crawler cùng lúc trên một thư mục `data/`.

Dừng tay: **Runtime → Interrupt execution** (tương đương Ctrl+C).

---

## Bước 5 — Sao lưu và khôi phục (quan trọng)

**Sao lưu tự động (khuyến nghị):** set `CRAWLER_BACKUP_DIR` + `CRAWLER_BACKUP_EVERY_IDS=1000` (hoặc `run_crawl.py --backup-dir … --backup-every-ids 1000`). Crawler copy `data/` lên Drive sau mỗi 1000 ID cơ sở đã xử lý trong lần chạy (manifest snapshot + `papers/`). Nếu Colab chết giữa chừng, mất tối đa ~1000 ID kể từ lần backup gần nhất — không thay ô Backup tay sau Interrupt.

**Sao lưu tay** (vẫn nên chạy trước khi tắt máy):

```bash
!mkdir -p /content/drive/MyDrive/arxiv-data
!rsync -a /content/data/ /content/drive/MyDrive/arxiv-data/
```

**Khôi phục** (mỗi session mới, **trước** crawl — trong `colab_run.ipynb` là ô riêng, luôn bật):

```bash
!mkdir -p /content/data /content/drive/MyDrive/arxiv-data
!rsync -a /content/drive/MyDrive/arxiv-data/ /content/data/
```

**Dừng an toàn:** **Runtime → Interrupt** → chạy ô **Backup** (đợi `rsync` xong) → có thể tắt máy. Phiên sau: restore → cùng lệnh crawl ở Bước 4.

---

## Bước 6 — Sau khi crawl (hoặc sau pilot)

```bash
!python validate_corpus.py --data-dir /content/data
!python export_data.py --data-dir /content/data
```

- `validate_corpus.py` thoát mã **0** = PASS (cấu trúc và quy ước ổn).
- `export_data.py` tạo bảng và thống kê trong `data/stats/` (Parquet + CSV) — dùng cho báo cáo Jupyter Book.

Log và số hiệu năng: `data/logs/crawl.log`, `data/logs/metrics.json`.

---

## Dữ liệu nằm ở đâu?

Trong `CRAWLER_DATA_DIR` (mặc định `/content/data` trên Colab):

```
data/
  manifest.sqlite      # trạng thái resume
  papers/<arxiv_id>/   # metadata, từng phiên bản, tex/bib, references
  logs/
  stats/               # sau khi chạy export_data.py
```

**Không** commit `data/`, key, hay file `.sqlite` lên GitHub.

---

## Khi gặp lỗi

| Triệu chứng | Việc nên làm |
| --- | --- |
| Thoát mã **2** | Thiếu `CRAWLER_CONTACT` hoặc lỗi đọc `id_ranges.csv` — sửa Bước 2 / 3 |
| Thoát mã **3** | arXiv **403**, hoặc **429** sau khi đã chờ `Retry-After` và thử lại tối đa 4 lần — **dừng**, chờ thêm **30–60 phút**, **Backup**, rồi chạy lại cùng lệnh (resume). **Không** giảm delay / mở thêm tab crawl |
| `validate_corpus` / `export_data` lỗi | Corpus **chưa crawl xong** (dừng giữa chunk, thiếu S2) — bình thường; chỉ validate khi crawl hết ID |
| S2 chậm / 429 | Cần `S2_API_KEY`; chờ backoff tự động; không spam request |
| Session Colab chết | Khôi phục từ Drive (Bước 5), resume cùng lệnh |
| `Mountpoint must not already contain files` khi `drive.mount` | Thường do chạy lại sau disconnect với `force_remount` hoặc mount FUSE treo. **Runtime → Restart session**, chạy lại notebook từ đầu; dùng logic mount trong `colab_run.ipynb` (bỏ qua nếu Drive còn dùng được) |
| Một vài cặp `failed` | Chạy lại cùng lệnh crawl; hoặc nhóm xử lý theo dải (T4.2) |

Cảnh báo `S2_API_KEY not set`: vẫn chạy được nhưng S2 bị giới hạn rất chặt (~100 request / 5 phút).

---

## Quy tắc lịch sự (bắt buộc)

- arXiv **chỉ** qua `export.arxiv.org`, **≥ 3 giây** giữa các request, **một** kết nối.
- Semantic Scholar **≥ ~1 giây** giữa các request (có key vẫn phải tuân thủ limiter trong code).
- User-Agent có thông tin liên hệ từ `CRAWLER_CONTACT`.

Chi tiết kỹ thuật: [`README.md`](../README.md), [`CONTRIBUTING.md`](../CONTRIBUTING.md), trạng thái dự án: [`STATUS.md`](../STATUS.md) (mục 0).

---

## Tóm tắt một dòng

**Secrets → mount Drive → pilot 30 ID → mỗi phiên: restore → crawl (cùng lệnh) → backup → tắt máy được → validate + export khi xong.**
