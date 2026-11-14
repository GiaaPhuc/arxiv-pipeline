# M3-D — Bàn giao ranker cho E

Owner: **D — Thụ** · Reviewer: **E — Triệu** · Nhánh: `m3/d-ranker` · Base: `main`.

## Phạm vi và giao diện

`MatchingRanker()` mặc định là LambdaMART 21 cột, khớp nguyên trạng
`RankerProtocol` và `ranker_factory` trong `matching.pipeline`. Không đổi hợp đồng
`models.py`, dữ liệu nhãn, splits, hoặc mã B/C/E.

| Cấu hình | Khởi tạo |
| --- | --- |
| Title Fuzzy | `MatchingRanker("fuzzy")` |
| TF-IDF | `MatchingRanker("tfidf")` |
| Logistic Regression | `MatchingRanker("logistic")` |
| LambdaMART 21 | `MatchingRanker()` |
| LambdaMART 18 | `MatchingRanker(include_m2=False)` |

- `train(features, labels, query_sources, *, valid_features=None, valid_labels=None)`
  dùng đúng kiểu dữ liệu của pipeline. `labels` phải có khóa
  `(arxivid, citationkey, ref_arxiv_id)` cho mọi dòng; không tự điền âm cho nhãn thiếu.
- `score_candidates(features)` trả điểm thô theo thứ tự đầu vào; dùng để phân tích
  riêng tác động mô hình và anchor.
- `predict_top5_for_query(features)` trả đúng 5 `str | None`.
- `predict_rows(features, labeled_queries)` trả `list[PredictionRow]` cho evaluator E;
  giữ nguyên partition và ground truth, kể cả query không có ứng viên.
- `export_metadata(path)` ghi JSON cấu hình, thứ tự đặc trưng, thời gian fit (giây),
  trọng số nguồn, trạng thái early stopping, số nhóm và gain importance (LambdaMART).
  Thời gian này không gồm đọc dữ liệu/trích đặc trưng, không phải benchmark toàn pipeline.

## Quyết định kỹ thuật

1. Gom nhóm theo **cả `(arxivid, citationkey)`**, thứ tự xuất hiện đầu tiên; giữ thứ tự
   dòng trong nhóm. Ma trận, nhãn, trọng số và ID cùng được sắp lại theo nhóm.
2. Chỉ dùng `FEATURE_COLUMNS`. Ablation bỏ đúng `cite_frequency`,
   `first_cite_section_type`, `first_cite_section_depth`, giữ thứ tự 18 cột còn lại.
3. Mọi ứng viên của một query có cùng trọng số **Manual=3, Auto=1**. LightGBM nhân
   gradient/Hessian mỗi dòng sau tính gradient của query, nên đây là cách tăng đóng
   góp cả query. Đã kiểm chứng bằng fit thật trên các query có nhãn xung đột: đổi
   nguồn Manual/Auto làm đảo chiều ưu tiên học được.
   [Mã nguồn LightGBM 4.7](https://github.com/microsoft/LightGBM/blob/v4.7.0/src/objective/rank_objective.hpp).
4. LambdaMART loại nhóm toàn âm; giữ mọi ứng viên của nhóm còn lại. Nhóm một ứng viên
   dương vẫn được giữ nhưng không sinh cặp học. Train phải có ít nhất một nhóm chứa
   cả dương và âm. Logistic dùng cả nhóm toàn âm nhưng yêu cầu Train có đủ hai lớp.
5. `StandardScaler` chỉ fit Train. Train/Valid trùng **bài** bị từ chối. LambdaMART
   chỉ early stop trên Valid-Manual có nhãn dương; thiếu tập này thì chạy số cây cố
   định (`n_estimators=100` mặc định). Caller chịu trách nhiệm không đưa Test vào Train/Valid.
6. arXiv/DOI có cùng tầng ưu tiên; trong từng tầng sắp điểm giảm dần, bằng điểm giữ
   vị trí gốc. Dedup sau sắp xếp, giữ occurrence có ưu tiên cao nhất; đệm `None`.
   Không sửa ID. ID rỗng, query bị trộn, feature/score không hữu hạn đều báo `ValueError`.
7. Seed 42, LightGBM một CPU thread, deterministic/force_col_wise. Hyperparameter
   khởi đầu chưa được tune trên dữ liệu thật. Chưa lưu/nạp model tự động qua CLI.

## So sánh năm cấu hình trên cùng splits

Dùng lại các biến đã được pipeline chia theo bài: `train_features`, `train_labels`,
`valid_features`, `valid_labels`, `test_features`, `test_queries`, `query_sources`.
Không gọi lại hàm chia tập cho từng mô hình. Đoạn dưới chạy khi evaluator E đã merge;
không thay evaluator bằng công thức khác.

```python
import json
from dataclasses import asdict
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from matching.evaluation import evaluate_mrr5  # chờ module E
from matching.export_hf import PREDICTIONS_SCHEMA
from matching.ranker import MatchingRanker

out_dir = Path("data/matching/range_0")  # đổi đúng range-index của D sau khi xác nhận
out_dir.mkdir(parents=True, exist_ok=True)
configs = [
    ("fuzzy", "fuzzy", True),
    ("tfidf", "tfidf", True),
    ("logistic", "logistic", True),
    ("lambdamart_21", "lambdamart", True),
    ("lambdamart_18", "lambdamart", False),
]
for name, model, include_m2 in configs:
    ranker = MatchingRanker(model, include_m2=include_m2)
    ranker.train(
        train_features,
        train_labels,
        query_sources,
        valid_features=valid_features,
        valid_labels=valid_labels,
    )
    predictions = ranker.predict_rows(test_features, test_queries)
    metrics = evaluate_mrr5(predictions, query_sources=query_sources, partition="test")
    pq.write_table(
        pa.Table.from_pylist([asdict(row) for row in predictions], schema=PREDICTIONS_SCHEMA),
        out_dir / f"{name}_predictions.parquet",
        compression="zstd",
    )
    ranker.export_metadata(out_dir / f"{name}_metadata.json")
    (out_dir / f"{name}_evaluation.json").write_text(
        json.dumps([asdict(metric) for metric in metrics], indent=2),
        encoding="utf-8",
    )
    # Tùy chọn lưu model LambdaMART, cùng metadata để giữ thứ tự cột:
    if model == "lambdamart":
        ranker.model_.booster_.save_model(str(out_dir / f"{name}_model.txt"))
```

Giữ riêng file dự đoán của từng cấu hình, không ghi đè `predictions.parquet` chính.
Mọi artifact nằm trong `data/` đã gitignore. `range_0` chỉ là ví dụ, chưa xác nhận
range-index cá nhân D. Metadata luôn ghi trạng thái mặc định `NOT EVALUATED` cho
đánh giá thật; số đo thật lưu riêng trong `*_evaluation.json` do E tính.

Để dùng cấu hình khác trong orchestrator qua Python:

```python
from matching.pipeline import default_matching_stages, run_matching_pipeline

stages = default_matching_stages()  # yêu cầu đủ module B/C/E
stages.ranker_factory = lambda: MatchingRanker(include_m2=False)
# Truyền stages=stages vào run_matching_pipeline với các đường dẫn hiện có.
```

## Giới hạn và việc tiếp theo

- Unit test dùng nguồn tổng hợp; 5 test tích hợp dùng ranker thật, B/C/E là các stage
  giả sẵn có trong test của A. Chưa chạy end-to-end với toàn bộ module thật.
- **MRR@5 dữ liệu thật: NOT EVALUATED**. Chưa xác nhận mục tiêu 0,85 hoặc thời gian
  train dưới 15 giây trên dải 5.000 ID.
- LightGBM 4.7 phát cảnh báo `eval_set` deprecated; vẫn dùng API này để tương thích
  dependency tối thiểu LightGBM 4.3 mà repo khai báo. Không tắt cảnh báo.
- 15/11: E review, merge module còn thiếu và smoke test end-to-end.
- 16–20/11: xác nhận range-index D, gán nhãn và dùng splits chính thức; đánh giá
  Manual/Auto/Overall, ablation 18/21, benchmark CPU, nộp Hugging Face cá nhân.
- `NullQueryDetector` chưa triển khai vì là tùy chọn.

## Kết quả kiểm chứng 14/11/2026

| Bộ kiểm | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| D unit/handoff | 42 | 0 | 0 |
| D tích hợp `ranker_factory` | 5 | 0 | 0 |
| Toàn repository | 661 | 0 | 0 |

Môi trường tạm `/tmp/m3-d-venv`: Python 3.14, LightGBM 4.7.0,
scikit-learn 1.9.1, Ruff 0.17.1; dùng requirements-ci của repo, không sửa dependency.

Lệnh đã chạy:

```sh
/tmp/m3-d-venv/bin/ruff check .
/tmp/m3-d-venv/bin/ruff format .
/tmp/m3-d-venv/bin/python -m pytest src/tests/test_ranker.py -q
HF_HOME=/tmp/m3-d-hf HF_DATASETS_CACHE=/tmp/m3-d-hf/datasets HF_HUB_OFFLINE=1 HF_DATASETS_OFFLINE=1 /tmp/m3-d-venv/bin/python -m pytest -q
```

47 test D đạt trong 2,71 giây; toàn repo 661 test đạt trong 14,63 giây.
Ruff check sạch, format không đổi 95 file. Có 4 cảnh báo deprecation LightGBM đã
mô tả ở trên. Full suite cần quyền socket cục bộ cho test multiprocessing; cache HF
phải được đặt trước khi import datasets. Lượt đầu sandbox chặn hai điều kiện này;
không sửa code M2/HF hoặc bỏ test để vượt kiểm tra.
