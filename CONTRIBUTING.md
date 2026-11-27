# Quy ước làm việc với repo mã

Repo: https://github.com/GiaaPhuc/arxiv-pipeline
Nhánh mặc định: `main`. Trạng thái dự án: `STATUS.md` (đọc mục 0).

File này là quy ước khi sửa mã. Khi đóng băng bản nộp theo yêu cầu gốc chỉ giữ mã nguồn, `requirements.txt` và `README.md`, các file làm việc (`STATUS.md`, `AGENTS.md`, `CONTRIBUTING.md`, `docs/`) được rà soát/gỡ theo checklist đóng băng nếu đề yêu cầu.

## Máy mới

```text
git clone https://github.com/GiaaPhuc/arxiv-pipeline.git
cd arxiv-pipeline
python -m venv .venv
.venv\Scripts\activate
pip install -r src/requirements-dev.txt
pre-commit install
pytest
```

Kỳ vọng: toàn bộ **816 unit tests** (`319` M1 + `334` M2 + `163` M3) đạt, không còn test bị skip. Có test đỏ thì báo người vai A, không sửa file của người khác.

## Nhánh và pull request

- Không đẩy thẳng `main`. Mọi thay đổi đi qua pull request.
- Tên nhánh: `m1/<vai>-<mô-tả-ngắn>`, `m2/<vai>-<mô-tả-ngắn>`, `m3/<vai>-<mô-tả-ngắn>`. Ví dụ `m1/b-ids`, `m2/c-hierarchy`, `m3/d-ranker`.
- Một PR, một việc, một reviewer. Reviewer là người khác owner module.
- Reviewer im quá 12 giờ và test xanh thì người vai A được merge.
- Commit bằng tài khoản của mình.
- Mẫu mô tả PR nằm ở `.github/pull_request_template.md`.

| Vai | M1 (`src/crawler/`) | M2 (`src/parsing/`) | M3 (`src/matching/`) |
| --- | --- | --- | --- |
| **A** | `models.py` (PR riêng), `http.py`, `manifest.py`, `config.py`, `pipeline.py`, `preflight.py`, `metrics.py`, `README.md` | `models.py`, `pipeline.py`, `src/run_parse.py` | `models.py`, `features.py`, `pipeline.py`, `export_hf.py`, `src/run_matching.py` |
| **B** | `ids.py`, `discovery.py`, `s2.py` | `cleaner.py`, `math_normalizer.py`, `sentence_splitter.py` | `cleaner.py`, `lexical_features.py` |
| **C** | `src/tests/conftest.py`, `source.py` | `gathering.py`, `hierarchy.py` | `labelling.py`, `auto_matcher.py` |
| **D** | `export.py`, `src/corpus.py` | `bib_extractor.py`, `dedup.py`, `export_m2.py` | `ranker.py`, `train_ranker.py` |
| **E** | `validate_corpus.py`, repo `arxiv-book` | `validate_m2.py`, `arxiv-book/m2/*` | `hierarchy_features.py`, `evaluation.py`, `plots.py` |

`models.py` (`crawler/models.py`, `parsing/models.py`, `matching/models.py`) là hợp đồng dùng chung. Đổi trường, trạng thái hoặc bố cục đĩa thì mở PR riêng, cả năm người thấy, và tăng `SCHEMA_VERSION` nếu đổi thứ đã ghi ra đĩa. Không sửa lặng trong PR của module mình.

## Trước khi mở PR

```text
ruff check .
ruff format --check .
pytest
```

Pre-commit chạy phần lint. Unit test nằm tại `src/tests/{m1,m2,m3}/`, không được gọi mạng thật: dùng `responses` và fixture dựng trong bộ nhớ.

## Không commit

- `data/`, `.env`, API key, `*.gz`, `*.parquet`, `*.sqlite`, gói tải từ `/src/`
- Khóa Semantic Scholar chỉ ở biến môi trường `S2_API_KEY` hoặc Colab Secrets
- Không dán key vào chat, PR, log

## Khi gọi mạng thật

Chỉ ở spike, smoke, preflight và crawl thật. Không gọi trong unit test.

- arXiv chỉ qua `export.arxiv.org`. Không gọi `arxiv.org`.
- **Một kết nối đơn**, `Start-to-Start >= 3,0 s` (mặc định `3,05 s + jitter 0..0,10 s` + đệm hậu tải `0,25 s`). API và `/src/` dùng chung một `RateLimiter`.
- Semantic Scholar: một worker, lô mặc định **150 ID** (tối đa `500`, tự chia đôi bisection khi gặp `400` size-limit/`413`/`5xx`/`>= 9.500` refs) qua `POST /graph/v1/paper/batch`; khoảng cách nền `>= 1,1 s`/request (`>= 1,0 s`), AIMD thích ứng sau `429` (`2→4→8→15 s` + backoff, tự tăng `+0,4 s` và hồi `-0,25 s` sau `5` lần `200`).
- Gặp **429** trên arXiv `/src/` hoặc API: chờ `Retry-After` (hoặc defer & cooldown tuyến tính `120→180→240 s` khi bật `--auto-resume-rate-limit`), tối đa 4 lần; vẫn 429 thì dừng (mã **3**). **403:** dừng ngay. Báo nhóm, không tăng số kết nối, không chạy lại dồn dập.
- User-Agent lấy từ `CRAWLER_CONTACT`. Đặt biến này trước khi crawl thật.

## Dải ID

File đang dùng: `src/id_ranges.csv` (5 dải, cả hai đầu, tổng 25.000 ID). Crawl đủ cả file trên **một máy** (D8): `python src/run_crawl.py --ranges src/id_ranges.csv`. Mỗi người chỉ theo dõi dải của mình; không bật crawl đầy đủ trên máy khác cùng lúc.

## Việc trên GitHub mà máy local chưa làm hộ

Ba việc này cấu hình trên github.com:

1. Mời đủ 5 thành viên quyền Write.
2. Bảo vệ `main`: bắt pull request, 1 approval, bắt check `lint-test` xanh.
3. Mở tab Actions, xác nhận workflow CI (`ruff check .`, `ruff format --check .`, `pytest`) xanh.
