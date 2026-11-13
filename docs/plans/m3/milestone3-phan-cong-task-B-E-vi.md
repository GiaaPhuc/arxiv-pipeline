# Phiếu Phân công Công việc Milestone 3 cho Cả Nhóm (A $\rightarrow$ E)

> **Dự án:** `arxiv-pipeline` — Milestone 3: Pipeline Đối sánh Tham chiếu (Reference Matching), Học máy Xếp hạng MRR@5, Công bố Hugging Face & Poster A2
> **Căn cứ kỹ thuật:** [`milestone3-implementation-plan-vi.md`](./milestone3-implementation-plan-vi.md) và đề bài [`milestone3-matching-vi.md`](../../project%20requirements/milestone3-matching-vi.md).
> **Mốc khóa tiến độ:** **Code-Complete trước 16/11 (hết 15/11 23:59)** · **Mỗi thành viên tự chạy 5.000 ID & nộp Dataset Hugging Face cá nhân: 20/11/2026 23:59** · **Poster A2 dọc chung (`.pptx`): 27/11/2026 23:59**.
> **Mục tiêu của bản phân công:** Chia Milestone 3 thành các module code độc lập cho **B, C, D, E** dưới sự điều phối và ghép nối của Leader **A** trong **1 repo GitHub chung** (xong toàn bộ mã trước **16/11**); sau đó từ **16–20/11**, **mỗi thành viên (A, B, C, D, E)** tự chạy trọn pipeline trên **dải 5.000 ID** của mình để nộp lên **Hugging Face cá nhân**, bảo đảm:
>
> 1. **Làm việc song song 100% (Zero-Blocking):** Nhờ khóa hợp đồng [`src/matching/models.py`](../../../src/matching/models.py) và bộ fixture chung `src/tests/matching_fixtures.py` ngay từ ngày 13/11, cả 5 thành viên viết code và chạy `pytest` độc lập hoàn toàn với nhau và đóng băng toàn bộ mã trước 16/11 mà không bị chặn bởi crawl 25k.
> 2. **Phủ kín 100% đầu việc & Tự chủ 5.000 ID / người:** Không còn module "mồ côi"; bộ mã chung hỗ trợ sẵn `--range-index <0..4>` giúp mỗi người tự gán nhãn ($\ge 5$ bài manual + $\ge 10\%$ auto), tự train/eval (`Test 1+1`, `Valid 1+1`, `Train`), và tự xuất 6 bảng Parquet của 5.000 ID lên HF cá nhân.
> 3. **Tuân thủ chặt đề bài:** Giữ nguyên cột `groundtrutharxivid` bất biến, dùng 100% mô hình/đặc trưng không pretrained, và kiểm thử đầy đủ schema 6 bảng Hugging Face.

---

## 1. Bảng Tổng quan Phân vai & Ma trận Review Chéo Milestone 3

| Thành viên | Trụ cột phụ trách (Role) | Module / File được phép sửa | Trọng tâm Kỹ thuật & Đầu ra (`Code chung <16/11` · `Chạy dải 5.000 ID 16–20/11`) | Người Review PR |
| :--- | :--- | :--- | :--- | :---: |
| **A — Phúc**<br/>*(Leader)* | **Hợp đồng, Blocking/Ghép Đặc trưng, Orchestrator & Xuất HF**<br/>*(Contract, Features Aggregator, Pipeline & HF)* | `src/matching/models.py`<br/>`src/matching/features.py`<br/>`src/matching/pipeline.py`<br/>`src/matching/export_hf.py`<br/>`src/run_matching.py`<br/>`src/tests/matching_fixtures.py`<br/>`src/tests/test_matching_pipeline.py`<br/>`src/tests/test_export_hf.py`<br/>`src/requirements*.txt`, `README.md` | 1. Khóa `models.py` (21 đặc trưng), `matching_fixtures.py` & `requirements*.txt`.<br/>2. `features.py` (`cdist` + blocking $n > 50$ + 3 relative) & `pipeline.py` hỗ trợ `--range-index <0..4>`.<br/>3. `export_hf.py`: lọc 6 bảng Parquet theo dải 5.000 ID + YAML + `datasets.load_dataset`; tự chạy & nộp HF cho dải 5.000 ID của A. | **Cả nhóm** (`models.py`) / **E** (pipeline & HF) |
| **B — Sơn** | **Trụ cột 1: Làm sạch Văn bản & 15 Đặc trưng Cặp**<br/>*(Entity Cleaner & Lexical/Entity/Temporal/Anchor Features)* | `src/matching/cleaner.py`<br/>`src/matching/lexical_features.py`<br/>`src/tests/test_matching_cleaner.py`<br/>`src/tests/test_lexical_features.py` | 1. `cleaner.py`: giải mã LaTeX accents, bỏ `{}`, NFKD folding, `AuthorTokens`.<br/>2. `lexical_features.py`: tính **15 đặc trưng cặp** (6 Title, 4 Author, 3 Temporal, 2 Anchor).<br/>3. (`16–20/11`): Tự gán $\ge 5$ bài + chạy `--range-index` cho dải 5.000 ID của B & nộp HF cá nhân. | **C** |
| **C — Quỳnh** | **Trụ cột 2: Khung Gán nhãn Chuẩn & Chia tập**<br/>*(Manual Labeling, Audited Auto-Matcher & Splits)* | `src/matching/labelling.py`<br/>`src/matching/auto_matcher.py`<br/>`src/tests/test_labelling.py` | 1. `labelling.py`, `auto_matcher.py` & `create_dataset_splits()` hỗ trợ `--range-index <0..4>` và audit P/R/F1.<br/>2. (`16–20/11`): Tự gán & nộp HF cho dải 5.000 ID của C; hỗ trợ kiểm tra chéo $\kappa \ge 0.95$ và tổng hợp P/R/F1 5 dải cho Poster. | **B** |
| **D — Thụ** | **Trụ cột 3: Mô hình Học máy Xếp hạng & Tối ưu Top-5**<br/>*(Supervised Ranking & Post-Processing)* | `src/matching/ranker.py`<br/>`src/matching/train_ranker.py`<br/>`src/tests/test_ranker.py` | 1. 3 baselines + **`LGBMRanker(lambdarank)`** + optional `NullQueryDetector`, thuần nhận `MatchFeatures`, hậu xử lý & đệm `None`.<br/>2. (`16–20/11`): Tự chạy dải 5.000 ID của D nộp HF cá nhân; phối hợp E chạy Ablation 18 vs 21 & tổng hợp MRR@5 toàn nhóm. | **E** |
| **E — Triệu** | **Trụ cột 4: Đặc trưng Cấu trúc M2, Đánh giá MRR@5 & Poster A2**<br/>*(M2 Hierarchy Features, Evaluator, Plots & Poster)* | `src/matching/hierarchy_features.py`<br/>`src/matching/evaluation.py`<br/>`src/matching/plots.py`<br/>`docs/poster/`<br/>`src/tests/test_hierarchy_features.py`<br/>`src/tests/test_evaluation.py` | 1. `hierarchy_features.py` (3 đặc trưng M2), `evaluation.py` (`mrr_at_5_all` & `mrr_at_5_matchable`), `plots.py`.<br/>2. (`16–20/11`): Tự chạy dải 5.000 ID của E nộp HF cá nhân.<br/>3. (`21–27/11`): Xuất biểu đồ 300 DPI 5 dải & hoàn thiện Poster `.pptx` A2 dọc chung. | **D** |

---

## 2. Nhiệm vụ và Giao diện Chi tiết Từng Thành viên

### 2.1. Thành viên A (Leader) — Hợp đồng, Ghép Đặc trưng, Orchestrator & Xuất HF
- **Mã Task:** `M3-A`
- **File phụ trách:** `src/matching/models.py`, `src/matching/features.py`, `src/matching/pipeline.py`, `src/matching/export_hf.py`, `src/run_matching.py`, `src/tests/matching_fixtures.py`, `src/requirements*.txt`, `README.md`
- **Giao diện cung cấp cho nhóm:**
  ```python
  def extract_paper_features(
      bib_items: list[PrecleanedBib],
      candidates: list[PrecleanedRef],
      citation_index: PaperCitationIndex,
      *,
      blocking_threshold_n: int = 50,
  ) -> list[MatchFeatures]: ...
  def export_hf_dataset(
      m1_stats_dir: Path,
      m2_parsed_dir: Path,
      predictions_path: Path,
      out_dir: Path,
      *,
      range_index: int | None = 0,  # 0..4 (5,000 ID cá nhân) hoặc None (tất cả 25k)
      ranges_csv: Path = Path("src/id_ranges.csv"),
      max_shard_mb: int = 80,
  ) -> Path: ...
  ```
- **Điều kiện Xong (Done When):**
  - **Trong ngày 13/11 (Phase 0):** `models.py`, `matching_fixtures.py` và `src/requirements.txt` + `src/requirements-ci.txt` được merge lên `main`, CI xanh.
  - **Trước 16/11 (hết 15/11 — Code-Complete):** Hoàn tất `features.py` (`rapidfuzz.process.cdist` + Stage 1 blocking khi $n > 50$ + 3 đặc trưng tương đối), `pipeline.py`, `run_matching.py`, `export_hf.py` (lọc 6 bảng Parquet theo `--range-index <0..4>` + YAML + `--range-index all` cho phân tích 25k) và `test_matching_pipeline.py` + `test_export_hf.py` pass 100%; chạy thông E2E trên subset `arxiv-data/`.
  - **16–20/11 (Hạn HF 20/11 23:59):** A tự chạy pipeline & đẩy repo HF cá nhân 5.000 ID của A (hỗ trợ B–E kiểm chứng `datasets.load_dataset`) và cập nhật `README.md` của repo mã chung.

---

### 2.2. Thành viên B — Làm sạch Thực thể & 15 Đặc trưng Cặp
- **Mã Task:** `M3-B`
- **File phụ trách:** `src/matching/cleaner.py`, `src/matching/lexical_features.py`, `src/tests/test_matching_cleaner.py`, `src/tests/test_lexical_features.py`
- **Giao diện cung cấp cho nhóm:**
  ```python
  def clean_title(title: str) -> str: ...
  def extract_author_tokens(authors: list[str]) -> AuthorTokens: ...
  def normalize_arxiv_id(raw_id: str | None) -> str | None: ...
  def preclean_bibtex(row: BibtexRow) -> PrecleanedBib: ...
  def preclean_reference(
      row: Reference, *, citing_arxivid: str, s2_doi: str | None = None
  ) -> PrecleanedRef: ...
  def compute_pair_features(
      bib: PrecleanedBib,
      ref: PrecleanedRef,
      *,
      tfidf_cosine: float = 0.0,
  ) -> dict[str, int | float]: ...
  def compute_paper_tfidf_matrix(
      bibs: list[PrecleanedBib], refs: list[PrecleanedRef]
  ) -> np.ndarray: ...
  ```
- **Điều kiện Xong (Done When — Trước 16/11):**
  - Giải mã sạch dấu LaTeX (`\'e`, `\"a`, `\c{c}`), gỡ `{}` và NFKD ASCII folding; `AuthorTokens` trích xuất đúng họ + chữ cái đầu tên (`first_initial`).
  - `compute_pair_features` + `compute_paper_tfidf_matrix` xuất chính xác **15 đặc trưng** (`6 Title + 4 Author + 3 Temporal + 2 Anchor`), an toàn khi thiếu năm/tác giả/DOI và không rò rỉ dữ liệu Test.
  - `test_matching_cleaner.py` và `test_lexical_features.py` pass 100%, `ruff` sạch.

---

### 2.3. Thành viên C — Khung Gán nhãn Chuẩn (Manual & Audited Heuristic)
- **Mã Task:** `M3-C`
- **File phụ trách:** `src/matching/labelling.py`, `src/matching/auto_matcher.py`, `src/tests/test_labelling.py`
- **Giao diện cung cấp cho nhóm:**
  ```python
  def run_manual_labeling_cli(
      paper_ids: list[str],
      bib_by_paper: dict[str, list[PrecleanedBib]],
      refs_by_paper: dict[str, list[PrecleanedRef]],
      out_path: Path,
  ) -> list[LabeledQuery]: ...
  def compute_cohens_kappa(
      labels_a: dict[tuple[str, str], str | None],
      labels_b: dict[tuple[str, str], str | None],
  ) -> float: ...
  def heuristic_match(bib: PrecleanedBib, candidates: list[PrecleanedRef]) -> str | None: ...
  def run_auto_matcher(
      paper_ids: list[str],
      bib_by_paper: dict[str, list[PrecleanedBib]],
      refs_by_paper: dict[str, list[PrecleanedRef]],
  ) -> list[LabeledQuery]: ...
  def audit_auto_matcher(
      manual_queries: list[LabeledQuery],
      bib_by_paper: dict[str, list[PrecleanedBib]],
      refs_by_paper: dict[str, list[PrecleanedRef]],
  ) -> dict[str, float]: ...  # precision, recall, f1
  def create_dataset_splits(
      manual_queries: list[LabeledQuery],
      auto_queries: list[LabeledQuery],
      *,
      seed: int = 42,
  ) -> dict[str, Partition]: ...
  ```
- **Điều kiện Xong (Done When):**
  - **Trước 16/11 (Code-Complete):** Hoàn thiện `labelling.py`, `auto_matcher.py` (an toàn khi `PrecleanedRef.year is None`, chống trùng 1-nhiều), `create_dataset_splits()` hỗ trợ lọc theo `--range-index <0..4>` và `test_labelling.py` pass 100%; gán nhãn nháp $\ge 5$ bài trên `arxiv-data/`.
  - **16–20/11 (Tự làm 5.000 ID & Điều phối chung):** C tự gán $\ge 5$ bài manual + chạy auto-matcher ($\ge 10\%$, kiểm toán P/R/F1) cho dải 5.000 ID của C và nộp HF cá nhân; hướng dẫn các thành viên A/B/D/E gán đủ 5 bài/dải, tổ chức kiểm tra chéo $\kappa \ge 0.95$ trên mẫu chung và tổng hợp bảng Audit P/R/F1 cho Poster.

---

### 2.4. Thành viên D — Mô hình Học máy Xếp hạng & Tối ưu Top-5
- **Mã Task:** `M3-D`
- **File phụ trách:** `src/matching/ranker.py`, `src/matching/train_ranker.py`, `src/tests/test_ranker.py`
- **Giao diện cung cấp cho nhóm:**
  ```python
  class MatchingRanker:
      def train(
          self,
          features: list[MatchFeatures],
          labels: dict[tuple[str, str, str], int],
          query_sources: dict[tuple[str, str], AnnotationSource],
          *,
          valid_features: list[MatchFeatures] | None = None,
          valid_labels: dict[tuple[str, str, str], int] | None = None,
      ) -> None: ...
      def predict_top5_for_query(
          self, query_features: list[MatchFeatures]
      ) -> list[str | None]: ...  # always length 5, None-padded
  ```
- **Điều kiện Xong (Done When):**
  - **Trước 16/11 (Code-Complete):** Cài đặt đủ 3 baselines (`Title Fuzzy`, `TF-IDF/BM25`, `LogisticRegression`), `LGBMRanker(lambdarank)` (lọc nhóm toàn 0, `sample_weight` Manual > Auto) + optional `NullQueryDetector`; `ranker.py` độc lập 100% (chỉ nhận `MatchFeatures`), hậu xử lý Hard Anchor Boost + dedup + đệm `None` khi $n < 5$ (hoặc $n = 0$); `test_ranker.py` pass 100%.
  - **16–20/11 (Chạy dải 5.000 ID & Ablation):** D tự chạy `run_matching.py` nộp HF cá nhân cho dải 5.000 ID của D; phối hợp E chạy Ablation Study (18 vs 21 đặc trưng có M2) trên cả 5 dải (đạt **`MRR@5_matchable >= 0.85`**) cho Poster.

---

### 2.5. Thành viên E — Đặc trưng Cấu trúc M2, Đánh giá MRR@5 & Poster A2
- **Mã Task:** `M3-E`
- **File phụ trách:** `src/matching/hierarchy_features.py`, `src/matching/evaluation.py`, `src/matching/plots.py`, `docs/poster/`, `src/tests/test_hierarchy_features.py`, `src/tests/test_evaluation.py`
- **Giao diện cung cấp cho nhóm:**
  ```python
  def build_paper_citation_index(
      elements: list[ElementRow],
      hierarchy: list[HierarchyRow],
      *,
      version_strategy: str = "latest",
  ) -> PaperCitationIndex: ...
  def extract_hierarchy_features(
      citation_key: str, index: PaperCitationIndex
  ) -> dict[str, int]: ...  # cite_frequency, first_cite_section_type, first_cite_section_depth
  def evaluate_mrr5(
      predictions: list[PredictionRow],
      ground_truth: dict[tuple[str, str], str | None] | None = None,
      query_sources: dict[tuple[str, str], AnnotationSource] | None = None,
      *,
      partition: str = "test",
  ) -> list[EvaluationMetrics]: ...  # returns overall, manual, auto slices
  ```
- **Điều kiện Xong (Done When):**
  - **Trước 16/11 (hết 15/11 — Code-Complete):** `build_paper_citation_index` duyệt $O(|E|+|H|)$ một lần/bài, chọn đúng `max(version)`, dùng regex `\cite` khớp `validate_m2.py`, trích xuất 3 đặc trưng M2 có fallback `(0, -1, 0)` khi thiếu AST; `evaluate_mrr5` hỗ trợ dòng toàn âm (`None`) và xuất đủ `mrr_at_5_all` + `mrr_at_5_matchable` cho `overall`/`manual`/`auto`; hoàn chỉnh mã `plots.py` sẵn sàng nhận JSON kết quả từ 5 dải.
  - **16–20/11 (Chạy dải 5.000 ID & Ablation):** E tự chạy pipeline & nộp HF cá nhân cho dải 5.000 ID của E; phối hợp D gom số liệu Ablation 18 vs 21 đặc trưng từ cả 5 dải.
  - **21–27/11 (Hạn Poster 27/11 23:59):** Chạy `plots.py` xuất biểu đồ 300 DPI từ kết quả final của 5 dải và hoàn thiện Poster PowerPoint (`.pptx`) khổ **A2 dọc ($420 \times 594\text{ mm}$)** chung cho cả nhóm.

---

## 3. Trình tự Phối hợp — Code Chung Trước 16/11 & Tự Chủ 5.000 ID / Người (16–20/11)

1. **Phase 0 (T6 13/11 — Xong trước 18:00, A phụ trách):** Merge PR khóa `src/matching/models.py`, `src/tests/matching_fixtures.py` và cập nhật `src/requirements.txt` + `src/requirements-ci.txt` lên `main`; CI xanh 100%.
2. **Phase 1 (Tối T6 13/11 – 22:00 T7 14/11 — Song song toàn đội):**
   - B (`cleaner.py`, `lexical_features.py`), C (`labelling.py`, `auto_matcher.py` + nhãn nháp 5 bài đầu trên `arxiv-data/`), D (`ranker.py`, `train_ranker.py`), E (`hierarchy_features.py`, `evaluation.py`, khung `plots.py`) mở nhánh `m3/<vai>-<mô-tả>` viết đủ code & offline unit tests, đẩy PR trước **22:00 14/11**.
   - A viết song song `features.py` và khung `export_hf.py` (hỗ trợ `--range-index <0..4>` theo `src/id_ranges.csv`) dựa trên `models.py` & fixtures.
3. **Phase 2 (CN 15/11 — Cổng Đóng băng Code-Complete hết 15/11 23:59):**
   - **Sáng (trước 12:00):** Review chéo và merge 100% PR của B, C, D, E vào `main`.
   - **Chiều–Tối (trước 23:59):** A ghép `features.py` + `pipeline.py` + `run_matching.py` + `export_hf.py`; E chốt code `plots.py`; chạy E2E Smoke Test trên subset `arxiv-data/` (kể cả kiểm thử `--range-index`); xác nhận `ruff check . && ruff format --check . && pytest` xanh 100% trước **16/11**.
4. **Phase 3A (16–20/11, Hạn Dataset HF Cá nhân 20/11 23:59 — Mỗi người tự làm 5.000 ID):**
   - **Từng thành viên (A, B, C, D, E)** nhận lát cắt M1/M2 thuộc dải 5.000 ID của mình (`--range-index 0..4` theo `src/id_ranges.csv`), tự chạy CLI `labelling.py` ($\ge 5$ bài manual trong dải) $\rightarrow$ `run_matching.py --range-index <k>` (auto-matcher $\ge 10\%$ bài trong dải + `Train/Valid(1+1)/Test(1+1)` + `LGBMRanker` + `evaluation.py`) $\rightarrow$ `export_hf.py --range-index <k>`, kiểm chứng `datasets.load_dataset` và đẩy lên **repo Hugging Face cá nhân** trước **20/11 23:59**.
   - Song song trong `17–19/11`, **C** tổ chức kiểm tra chéo $\kappa \ge 0.95$ và **D, E** chạy tổng hợp Ablation 18 vs 21 đặc trưng trên 5 dải; **A** cập nhật `README.md` của repo mã chung trước **20/11 23:59**.
5. **Phase 3B (21–27/11, Hạn Poster 27/11 23:59):** E chạy `plots.py` xuất biểu đồ 300 DPI từ kết quả tổng hợp 5 dải, hoàn tất file `.pptx` A2 dọc; cả nhóm rà soát chéo số liệu và diễn tập trình bày trước **27/11 23:59**.
