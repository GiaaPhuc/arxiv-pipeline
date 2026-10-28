# Plan cải thiện luồng Semantic Scholar – giảm thời gian chờ 429

*Ngày 28/10/2026 · Dựa trên tài liệu mô tả dự án và luồng xử lý hiện tại (nhánh `m1/a-pipeline`, 177 test pass, chưa crawl thật)*

S2 chậm vì mỗi bài tốn ít nhất một request ở 1 req/s, và mỗi lần dính 429 phải chờ khoảng 60 s. Plan này chỉ sửa luồng S2: gộp request theo lô, giảm cơ hội dính 429 và xử lý 429 rẻ hơn. Phần arXiv giữ nguyên.

## 1. Mục tiêu và ràng buộc

**Mục tiêu:** hoàn tất S2 cho 25.000 ID trong vài giờ thay vì vài chục giờ, và thời gian chờ 429 giảm xuống mức không đáng kể.

**Ràng buộc không được vi phạm:**

- Hạn mức key: 1 request mỗi giây, cộng dồn trên mọi endpoint (batch, `/references`, venue). Mọi process dùng chung key phải chia nhau hạn mức này, nên tại một thời điểm chỉ một process gọi S2.

* Vẫn tuân `Retry-After` của S2: không rút ngắn thời gian chờ sau 429 và không retry dồn dập. Thời gian giảm được nhờ ít lần dính 429 hơn, không nhờ chờ ngắn hơn.
* Giữ mọi reference trên máy, kể cả ref không có arXiv ID. Chỉ lọc ref có arXiv ID khi export.
* Giữ `s2_scope: "paper"`: một lần fetch cho mỗi base ID, `references.json` giống nhau trong mọi `vK/`.
* Giữ marker `s2_references.raw.json.gz` là dấu hiệu hoàn thành S2. Không đổi ngầm `models.py` (`SCHEMA_VERSION=1`); đổi layout raw/refs cần PR và bump version.
* Không commit key hay `data/`. Không chạy hai process cùng `data/` (D8).
* Ngoài phạm vi: mọi thay đổi phía arXiv (limiter 3 s, discovery, `/src/`).

## 2. Vì sao S2 chậm và hay chờ 429

Thời gian chờ 429 cộng dồn từ ba nguyên nhân: quá nhiều request, hạn mức API key vẫn có thể bị vượt, và worker xử lý 429 theo kiểu chặn cứng.

| Nguyên nhân | Hiện tượng | Hệ quả |
| --- | --- | --- |
| Từ 1 request trở lên mỗi bài | 25.000 bài × 2–5 request (references, phân trang `limit=100`, venue) ≈ 50.000–125.000 request | Mỗi request là một cơ hội dính 429 |
| API key (R3, nay đã có) | Trước khi có key dùng chung pool với người dùng ẩn danh; nay đã có hạn mức riêng | 429 vẫn xảy ra nếu vượt hạn mức riêng, nên vẫn cần giảm số request |
| Chờ 429 chặn cả worker | Mỗi lần chờ 30–600 s (khoảng 60 s theo thực tế của bạn); chỉ có một worker nên mọi bài sau đều đứng chờ | 100 lần 429 ≈ 1,7 h chờ |
| Thử lại tối đa 4 lần mỗi bài rồi bỏ | 429 liên tiếp làm bài "give up" | 5 bài give-up liên tiếp thì worker bị tắt, phải resume thủ công |
| Limiter cố định 1 s | Không giảm tốc sau khi bị 429 | Vừa hết chờ lại dính 429 tiếp |
| Không có chế độ S2-only | Resume S2 phải duyệt lại 25k ID qua vòng arXiv | Thêm overhead, S2 phụ thuộc arXiv đi tới bài đó |

Kết luận: cách giảm thời gian thật là giảm số request khoảng 200–500 lần bằng batch, dùng API key đã có, và để limiter tự giảm tốc sau 429. Thời gian chờ của mỗi lần 429 do S2 quy định nên không rút ngắn được.

## 3. Các hạng mục cải thiện S2

### S1. Nạp API key S2 vào máy crawl (đã có key)

- Đặt key đã có vào biến môi trường `S2_API_KEY`. Code đã gửi header `x-api-key` khi có key.
- Có key thì dùng hạn mức riêng thay vì pool chung với người dùng ẩn danh, nên 429 ít hơn. Hạn mức của key là 1 request mỗi giây, cộng dồn trên mọi endpoint. Pilot cần xác nhận key hoạt động.
- Key không thay thế được batch: key chỉ nâng hạn mức, còn batch mới giảm số request, nên vẫn làm S2 và S3.

### S2. Gộp request bằng batch

- Dùng `POST /graph/v1/paper/batch` với `ids: ["ARXIV:<base id>", ...]`. Lô khoảng 100 ID (API cho tối đa 500, nhưng lô nhỏ giữ response vừa phải và dễ retry).
- `fields`: `venue` ở cấp paper cùng `references.paperId,references.externalIds,references.title,references.authors,references.publicationDate,references.year`. Cách này bỏ luôn request `fetch_venue`.
- 25.000 bài còn khoảng 250 request thay vì 50.000–125.000, tức số cơ hội dính 429 giảm 200–500 lần.
- Phần tử `null` trong kết quả tương đương 404 hiện tại: ghi raw `{status: 404}`.
- Bài có ref nghi bị cắt (chạm trần số ref lồng nhau) thì fallback sang `/references` với `limit=1000` (đổi từ 100 để giảm round-trip).
- Code cần đổi: `s2.py` thêm `fetch_references_batch(ids)` và parser cho phần tử ref dạng trực tiếp (khác `/references` vốn bọc trong `citedPaper`); `pipeline.py` cho `_s2_worker` gom job thành lô thay vì xử lý từng bài.
- Cần xác minh bằng pilot: giới hạn dung lượng response và số ref lồng nhau của batch, vì tôi chưa kiểm chứng được hai con số này.

### S3. Xử lý 429 thích ứng thay vì chặn cứng

- Chờ đúng giá trị `Retry-After` do server trả, không chờ ngắn hơn. Cooldown dùng chung cho cả worker nên không có request nào thử lại trong lúc chờ.
- Vì hạn mức là 1 request mỗi giây nên khoảng cách nền đặt 1,1 s (dư một chút cho độ trễ mạng) và không có cách chạy nhanh hơn. Sau mỗi 429 tăng khoảng cách giữa các request (1,1 s → 2 s → 4 s, trần 5 s) rồi giảm dần sau khoảng 50 request thành công liên tiếp, kèm jitter nhỏ. Mục đích là không dính 429 tiếp ngay khi vừa hết chờ.
- Tách rõ hai loại lỗi: 429 là tạm thời, giữ nguyên lô và thử lại sau cooldown, không tính vào "give up"; chỉ 403 (key sai hoặc bị chặn) mới tắt worker và báo nhóm.
- Thay quy tắc "5 bài give-up liên tiếp thì tắt worker" bằng ngưỡng tổng thời gian chờ liên tiếp không có request thành công (ví dụ 30 phút) rồi dừng và báo nhóm.
- Ghi vào `metrics.json`: số lần 429, tổng giây chờ, số request thành công, số lô.

### S4. Trạng thái S2 bền vững và chế độ S2-only

- Hiện hàng đợi chỉ nằm trong RAM và hoàn thành S2 = có `raw.gz`. Thêm file trạng thái riêng `data/s2_state.sqlite` (không dùng chung `manifest.sqlite` để không đụng process arXiv) với các cột `paper_id`, `status` (`pending`/`ok`/`not_found`/`failed`), `attempts`, `next_retry_at`, `last_error`. Marker `raw.gz` vẫn là dấu hoàn thành cuối cùng.
- Thêm cờ `--s2-only`: `python src/run_crawl.py --s2-only --ranges src/id_ranges.csv`. Lệnh này lấy danh sách ID, bỏ ID đã `ok` hoặc `not_found`, gom lô và gọi batch, không gọi arXiv.
- Vì S2 khóa theo base ID nên chế độ này chạy được ngay, không cần chờ arXiv. Vì key chỉ cho 1 request mỗi giây cho mọi process, khi chạy chế độ này phải tắt worker S2 trong crawler arXiv (thêm cờ --no-s2), nếu không hai process cộng dồn vượt hạn mức và gây 429. Kết quả ghi vào `data/s2_staging/<dirname>/s2_references.raw.json.gz`.
- Bước hợp nhất (materialize): sau khi có `paper.json` và biết số phiên bản, ghi `references.json` vào mọi `vK/`, cập nhật `venue` trên `paper.json`, rồi đặt `raw.gz` vào `papers/<dirname>/` làm marker cuối cùng. Chạy bước này khi crawler arXiv đã dừng để không vi phạm D8.

### S5. Phân biệt 404 và lỗi tạm

- 404 hoặc `null` nghĩa là bài chưa được S2 index: ghi `{status: 404}` vào raw, trạng thái `not_found`, không retry trừ khi chủ động chạy lại.
- Lỗi mạng, 5xx hoặc 429 kéo dài: trạng thái `failed` kèm `next_retry_at`, thử tối đa N lần (ví dụ 5) rồi để lại cho lần chạy sau thay vì thử vô hạn.
- Cuối mỗi lần chạy in báo cáo số bài `ok`, `not_found`, `failed`.

## 4. Ước lượng trước và sau

Ước lượng thô, cần cập nhật sau pilot. Số lần 429 ở hàng cuối chỉ là ví dụ minh họa, chưa đo. Hạn mức 1 request mỗi giây là trần cứng: 50.000–125.000 request đơn lẻ không thể nhanh hơn khoảng 14–35 h, chỉ batch mới phá được trần này.

| Hạng mục | Trước | Sau |
| --- | --- | --- |
| Số request S2 cho 25.000 bài | 50.000–125.000 | khoảng 250–400 (lô 100 + fallback) |
| Thời gian S2, chưa tính 429 | 15–40+ h | vài chục phút đến 1–2 h (giả định mỗi lô mất vài giây đến vài chục giây) |
| Cơ hội dính 429 | mỗi request | giảm 200–500 lần |
| Thời gian chờ 429 (ví dụ) | 100 lần × 60 s ≈ 1,7 h | 10 lần × 60 s = 10 phút |
| Resume chỉ S2 | duyệt lại 25k ID qua vòng arXiv | `--s2-only`, chỉ bài `pending` hoặc `failed` |

## 5. Lộ trình triển khai

| Thứ tự | Công việc | Đầu ra |
| --- | --- | --- |
| 1 | Pilot 30 ID: `python src/run_crawl.py --ranges src/id_ranges.csv --limit 30` | Số request/bài thật, số 429, tổng giây chờ, % 404, % cần venue |
| 2 | Đã có key: xác nhận đặt `S2_API_KEY` trên máy crawl | Có key |
| 3 | Cài batch trong `s2.py` và `pipeline.py`, kèm test fixture | PR; 177 test cũ vẫn pass cùng test mới |
| 4 | Pilot batch 30 ID, đối chiếu với đường `/references` cũ | Cùng số ref và cùng `ref_arxiv_id` |
| 5 | Limiter thích ứng, xử lý 429, ghi metrics (S3) | PR, test mô phỏng 429 bằng `responses` |
| 6 | `--s2-only`, state SQLite, staging, materialize (S4, S5) | PR |
| 7 | Chạy S2 pass cho 25k ID (chỉ song song với crawler arXiv khi crawler đã tắt worker S2, vì key chỉ cho 1 request mỗi giây) | `data/s2_staging/` đủ ID |
| 8 | Materialize, `validate_corpus.py`, `export_data.py` | Corpus đầy đủ và thống kê |

**Nếu ít thời gian:** làm 3, 4 trước vì batch mang lại phần lớn lợi ích, rồi đến 5. Bước 6 có thể làm sau; khi đó chạy batch ngay trong `_s2_worker` hiện tại.

## 6. Rủi ro và cách giảm

| Rủi ro | Cách giảm |
| --- | --- |
| Batch cắt bớt ref hoặc response quá lớn | Lô 100; phát hiện chạm trần để fallback `/references` với `limit=1000`; đo trong pilot |
| Parser batch khác `/references` làm lệch dữ liệu | Test fixture và đối chiếu `ref_arxiv_id` giữa hai đường trên 30 ID |
| Đã có key nhưng vẫn dính 429 | Batch giảm mạnh số request; limiter thích ứng; ngưỡng thời gian chờ rồi dừng và báo nhóm |
| S2 trả 403 (key sai hoặc bị chặn) | Tắt worker, báo nhóm, không retry |
| Đổi raw hoặc state làm hỏng export/M3 | Giữ định dạng `raw.gz` tương thích `export.py`; nếu đổi layout thì bump `SCHEMA_VERSION` qua PR |
| Staging lệch với `papers/` (ID không tồn tại trên arXiv) | Materialize chỉ cho ID đã có `paper.json`; ID arXiv `not_found` bị bỏ qua và ghi vào báo cáo |

## 7. Tiêu chí hoàn thành

- 177 test cũ vẫn pass; thêm test cho parser batch, phần tử `null`, fallback khi chạm trần, 429 có `Retry-After`, và resume bằng `--s2-only`.
- Pilot 30 ID: batch cho cùng số ref và cùng `ref_arxiv_id` như đường `/references` cũ.
- Tổng số request S2 cho 25.000 bài chỉ vài trăm (đọc từ `metrics.json`).
- Sau materialize, mọi bài có `s2_references.raw.json.gz` (kể cả `{status: 404}`) và `references.json` trong mọi `vK/`.
- `validate_corpus.py` không báo lỗi; `metrics.json` có số lần 429 và tổng giây chờ.

## 8. Câu hỏi cần chốt

1. Có xin nâng hạn mức của key trên 1 request mỗi giây được không? Nếu được, batch và limiter nên chỉnh lại theo hạn mức mới.
2. Thời gian chờ 429 thực tế là giá trị `Retry-After` do server trả hay khoảng 60 s cố định? Pilot sẽ đo, nhưng nếu đã có log thì cho biết sớm.
3. Có chấp nhận file trạng thái riêng `data/s2_state.sqlite` và thư mục staging không, hay chỉ muốn đổi worker sang batch trong luồng hiện tại?
4. Có cho chạy S2-only song song với crawler arXiv không (khi đó crawler arXiv phải tắt worker S2 vì hai process dùng chung 1 request mỗi giây của key), hay chỉ chạy sau khi arXiv dừng?
