# Quy ước làm việc với repo mã

Repo: https://github.com/GiaaPhuc/arxiv-pipeline
Nhánh mặc định: `main`. Trạng thái dự án: `STATUS.md` (đọc mục 0).

File này là quy ước khi sửa mã. Khi nộp Milestone 1, repo chỉ được giữ mã, `requirements.txt` và `README.md` — file này được gỡ cùng `STATUS.md` và `AGENTS.md` ở bước đóng băng (T5.3).

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

Kỳ vọng: toàn bộ test đạt, không còn test bị skip (đếm số ở `STATUS.md`). Có test đỏ thì báo người vai A, không sửa file của người khác.

## Nhánh và pull request

- Không đẩy thẳng `main`. Mọi thay đổi đi qua pull request.
- Tên nhánh: `m1/<vai>-<mô-tả-ngắn>`. Ví dụ `m1/b-ids`.
- Một PR, một việc, một reviewer. Reviewer là người trong bảng dưới, không phải người viết module.
- Reviewer im quá 12 giờ và test xanh thì người vai A được merge.
- Commit bằng tài khoản của mình.
- Mẫu mô tả PR nằm ở `.github/pull_request_template.md`.

| Vai | Được sửa | Reviewer |
| --- | --- | --- |
| A | `models.py` (PR riêng), `http.py`, `manifest.py`, `config.py`, `pipeline.py`, `metrics.py`, README | C |
| B | `ids.py`, `discovery.py` | D |
| C | `tests/conftest.py`, `source.py` | B |
| D | `s2.py`, `export.py`, phần xuất của `corpus.py` | E |
| E | `validate_corpus.py`, repo sách (repo khác) | A |

`models.py` là hợp đồng dùng chung. Đổi trường, trạng thái hoặc bố cục đĩa thì mở PR riêng, cả năm người thấy, và tăng `SCHEMA_VERSION` nếu đổi thứ đã ghi ra đĩa. Không sửa lặng trong PR của module mình.

## Trước khi mở PR

```text
ruff check .
ruff format .
pytest
```

Pre-commit chạy phần lint. Unit test không được gọi mạng thật: dùng `responses` và fixture dựng trong bộ nhớ. Ca đang `skip` là việc của owner; bỏ `pytestmark = pytest.mark.skip` khi bắt đầu viết test thật.

## Không commit

- `data/`, `.env`, API key, `*.gz`, `*.parquet`, `*.sqlite`, gói tải từ `/src/`
- Khóa Semantic Scholar chỉ ở biến môi trường `S2_API_KEY` hoặc Colab Secrets
- Không dán key vào chat, PR, log

## Khi gọi mạng thật

Chỉ ở spike, smoke và pilot. Không gọi trong unit test.

- arXiv chỉ qua `export.arxiv.org`. Không gọi `arxiv.org`.
- Cách nhau ≥ 3 giây, tính từ lúc yêu cầu trước kết thúc. Một kết nối. API và `/src/` chung một limiter.
- Semantic Scholar ≥ 1 yêu cầu/giây khi có key. Gặp 429 thì chờ `Retry-After` (30–600 giây) rồi thử lại tối đa 4 lần cho bài đó; hết lần thì bỏ bài đó ở lần chạy này (lần sau tự làm lại). 403 (key sai) hoặc 5 bài liên tiếp bị bỏ thì tắt worker S2 trong lần chạy này, arXiv vẫn chạy tiếp.
- Gặp 403 hoặc 429 trên arXiv: pipeline tự dừng (mã thoát 3) và in tóm tắt. Báo nhóm, không tăng số kết nối, không chạy lại dồn dập.
- User-Agent lấy từ `CRAWLER_CONTACT`. Đặt biến này trước khi crawl thật.

## Dải ID

File đang dùng: `src/id_ranges.csv` (5 dải, cả hai đầu, tổng 25.000 ID). Mẫu định dạng: `src/id_ranges.example.csv`. Crawl đủ cả file trên **một máy** (D8): `python src/run_crawl.py --ranges src/id_ranges.csv`. Mỗi người chỉ theo dõi dải của mình; không bật crawl đầy đủ trên máy khác cùng lúc.

## Mỗi ngày

Trước 21:00, một tin ba dòng:

```text
Hôm qua: ...
Hôm nay: ... (mã task)
Kẹt: không / đang kẹt ở ... quá ... giờ
```

Kẹt quá 4 giờ thì nhắn ngay, không đợi 21:00.

## Việc trên GitHub mà máy local chưa làm hộ

Ba việc này bấm trên github.com trước khi ai mở nhánh:

1. Mời bốn người còn lại, quyền Write.
2. Bảo vệ `main`: bắt pull request, 1 approval, bắt check `lint-test` xanh.
3. Mở tab Actions, xác nhận lần chạy mới nhất xanh.
