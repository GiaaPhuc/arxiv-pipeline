# Phiếu Phân công Công việc Milestone 3 cho 4 Thành viên (B $\rightarrow$ E)

> **Dự án:** `arxiv-pipeline` — Milestone 3: Pipeline Đối sánh Tham chiếu (Reference Matching), Học máy Xếp hạng MRR@5, Công bố Hugging Face & Poster A2
> **Căn cứ kỹ thuật:** [`milestone3-implementation-plan-vi.md`](./milestone3-implementation-plan-vi.md) và đề bài [`milestone3-matching-vi.md`](../project%20requirements/milestone3-matching-vi.md).
> **Mục tiêu của bản phân công:** Chia bài toán Milestone 3 thành **4 trụ cột độc lập cho B, C, D, E** (dưới sự điều phối và ghép nối pipeline của Leader **A**), đảm bảo:
>
> 1. **Làm việc song song 100% (Zero-Blocking):** Dựa trên hợp đồng dữ liệu chuẩn `src/matching/models.py`, cả 4 người B, C, D, E có thể code và chạy `pytest` độc lập mà không bị phụ thuộc vào tiến độ của nhau.
> 2. **Tối ưu hóa điểm số và tiêu chí phân hóa:** Khai thác triệt để các đặc trưng cấu trúc phân cấp từ M2, xây dựng ground truth chất lượng cao, mô hình LTR GBDT vượt trội và thiết kế poster khoa học A2 dọc nổi bật.
> 3. **Lịch sử đóng góp đồng đều:** Mỗi thành viên đều có mã nguồn `.py`, bộ kiểm thử unit tests riêng trong `src/tests/` và đóng góp trực tiếp vào nội dung poster / dataset card.

---

## 1. Bảng Tổng quan Phân vai & Ma trận Review Chéo Milestone 3

| Thành viên | Trụ cột phụ trách (Role) | Module / File được phép sửa | Ý tưởng & Trọng tâm Kỹ thuật (Điểm nhấn Chấm bài) | Người Review PR |
| :--- | :--- | :--- | :--- | :--- |
| **B — Sơn** | **Trụ cột 1: Tiền xử lý & Kỹ thuật Đặc trưng Từ vựng**<br/>*(Cleaner & Lexical Features)* | `src/matching/cleaner.py`<br/>`src/matching/lexical_features.py`<br/>`src/tests/test_matching_cleaner.py`<br/>`src/tests/test_lexical_features.py` | 1. **Academic Entity Cleaner:** Khử toàn bộ lệnh macro LaTeX (`\textit`, `\textbf`, `\c{c}`, `\'e`), loại stop-words học thuật, chuẩn hóa họ tác giả đa văn hóa.<br/>2. **Lexical & Identifier Feature Suite:** Trích xuất 9 đặc trưng chuỗi (Levenshtein, Token Sort, Jaccard 3-gram, TF-IDF Cosine, mỏ neo `arXiv ID` và `DOI`). | **C** |
| **C — Quỳnh** | **Trụ cột 2: Khung Gán nhãn Chuẩn (Manual & Heuristic)**<br/>*(Ground Truth Annotation & Partitioning)* | `src/matching/labelling.py`<br/>`src/matching/auto_matcher.py`<br/>`src/tests/test_labelling.py` | 1. **Interactive Manual Labeling Tool:** Công cụ CLI/UI tương tác gán nhãn cho $\ge 5$ bài ($\ge 20$ mục/bài, tổng $\ge 100$ nhãn).<br/>2. **Cascade Heuristic Matcher:** Bộ đối sánh tự động 3 cấp (Regex `arXiv ID` $\rightarrow$ DOI exact $\rightarrow$ Fuzzy Title $\ge 0,95$ + Author match) gán nhãn $\ge 10\%$ số bài ngoài tập thủ công.<br/>3. **Dataset Splitter:** Phân chia Train/Valid/Test cấp bài báo không rò rỉ. | **B** |
| **D — Thụ** | **Trụ cột 3: Học máy Xếp hạng & Tối ưu Top-5 MRR**<br/>*(Supervised Ranking & MRR Optimization)* | `src/matching/ranker.py`<br/>`src/matching/train_ranker.py`<br/>`src/tests/test_ranker.py` | 1. **LightGBM LambdaMART Ranker:** Định hình bài toán LTR theo nhóm truy vấn BibTeX, tối ưu trực tiếp NDCG/MRR.<br/>2. **Confidence-Calibrated Top-5:** Hậu xử lý kết hợp Hard Anchor Boost và lọc ngưỡng tin cậy $\theta_{\text{null}}$ cho các dòng toàn âm.<br/>3. **Mô hình So sánh:** Cài đặt đầy đủ 3 baseline (Heuristic, BM25/TF-IDF, Logistic Regression) để làm bảng so sánh trên Poster. | **E** |
| **E — Triệu** | **Trụ cột 4: Đặc trưng Cấu trúc M2, Bộ Đánh giá & Poster A2**<br/>*(Hierarchy Features, Evaluator & Poster)* | `src/matching/hierarchy_features.py`<br/>`src/matching/evaluation.py`<br/>`docs/poster/`<br/>`src/tests/test_evaluation.py` | 1. **Hierarchy-Aware Feature Extractor:** Trích xuất tần suất trích dẫn, vị trí xuất hiện đầu tiên (Intro/Related Work/Method) từ cây phân cấp M2.<br/>2. **MRR@5 Evaluator & Error Analysis:** Đo lường chính xác MRR@5 theo đề bài, phân tích ma trận nhầm lẫn và các trường hợp lỗi.<br/>3. **Poster Khổ A2 Dọc ($420 \times 594\text{ mm}$):** Thiết kế file `.pptx` hoàn chỉnh với 5 khối khoa học sắc nét. | **D** |
| **A — Phúc** *(Leader)* | **Điều phối, Hợp đồng Dữ liệu & Xuất bản Hugging Face**<br/>*(Orchestration & HF Exporter)* | `src/matching/models.py`<br/>`src/matching/export_hf.py`<br/>`src/run_matching.py`<br/>`src/tests/test_matching_pipeline.py` | 1. Khóa hợp đồng dữ liệu `src/matching/models.py`;<br/>2. Ghép nối pipeline đa luồng và điều phối toàn bộ luồng chạy;<br/>3. Đóng gói 6 bảng Parquet phẳng và tạo `README.md` Dataset Card trên Hugging Face. | **A** (cả nhóm) |

---

## 2. Nhiệm vụ và Giao diện Chi tiết Từng Thành viên

### 2.1. Thành viên B — Tiền xử lý & Kỹ thuật Đặc trưng Từ vựng
- **Mã Task:** `M3-B`
- **File phụ trách:** `src/matching/cleaner.py`, `src/matching/lexical_features.py`
- **Giao diện cung cấp cho nhóm:**
  ```python
  def clean_title(title: str) -> str: ...
  def extract_author_surnames(authors: list[str]) -> set[str]: ...
  def compute_lexical_features(bib_item: BibtexRow, ref_item: Reference) -> dict[str, float]: ...
```
- **Điều kiện Xong (Done When):**
  - Xử lý sạch 100% các ký tự LaTeX escaped, ngoặc `{}` và stop-words học thuật.
  - Tính toán chính xác 9 đặc trưng chuỗi và định danh.
  - Bộ unit test `test_matching_cleaner.py` và `test_lexical_features.py` đạt 100% pass, không lỗi `ruff`.

---

### 2.2. Thành viên C — Khung Gán nhãn Chuẩn (Manual & Heuristic)
- **Mã Task:** `M3-C`
- **File phụ trách:** `src/matching/labelling.py`, `src/matching/auto_matcher.py`
- **Giao diện cung cấp cho nhóm:**
  ```python
  def run_manual_labeling_cli(papers: list[str], bib_table: dict, ref_table: dict) -> None: ...
  def run_auto_matcher(
      paper_ids: list[str], bib_table: dict, ref_table: dict
  ) -> list[CandidatePair]: ...
  def create_dataset_splits(
      manual_papers: list[str], auto_papers: list[str]
  ) -> dict[str, Partition]: ...
```
- **Điều kiện Xong (Done When):**
  - Hoàn tất bộ gán nhãn thủ công cho $\ge 5$ bài ($\ge 20$ mục/bài, tổng $\ge 100$ nhãn).
  - Cascade Heuristic Matcher tự động gán nhãn $100\%$ mục BibTeX cho $\ge 10\%$ bài ngoài tập thủ công.
  - Phân chia tập Train/Valid/Test nghiêm ngặt theo cấp bài báo, không rò rỉ dữ liệu.

---

### 2.3. Thành viên D — Mô hình hóa Học máy Xếp hạng & Tối ưu Top-5 MRR
- **Mã Task:** `M3-D`
- **File phụ trách:** `src/matching/ranker.py`, `src/matching/train_ranker.py`
- **Giao diện cung cấp cho nhóm:**
  ```python
  class MatchingRanker:
      def train(self, X_train: pd.DataFrame, y_train: np.ndarray, groups: np.ndarray) -> None: ...
      def predict_top5(
          self, bib_item: BibtexRow, candidates: list[Reference]
      ) -> list[str | None]: ...
```
- **Điều kiện Xong (Done When):**
  - Cài đặt và huấn luyện thành công mô hình `LGBMRanker` với mục tiêu LambdaMART.
  - Cài đặt 3 mô hình baseline để so sánh trên poster.
  - Dự đoán danh sách Top-5 ứng viên (`cand1` $\dots$ `cand5`) đạt $\text{MRR@5} \ge 0,85$ trên Test Set.

---

### 2.4. Thành viên E — Đặc trưng Cấu trúc M2, Bộ Đánh giá & Thiết kế Poster A2
- **Mã Task:** `M3-E`
- **File phụ trách:** `src/matching/hierarchy_features.py`, `src/matching/evaluation.py`, `docs/poster/`
- **Giao diện cung cấp cho nhóm:**
  ```python
  def extract_hierarchy_features(
      citation_key: str, elements: list[ElementRow], hierarchy: list[HierarchyRow]
  ) -> dict[str, float]: ...
  def evaluate_mrr5(
      predictions: list[PredictionRow], ground_truth: dict[tuple[str, str], str]
  ) -> EvaluationMetrics: ...
```
- **Điều kiện Xong (Done When):**
  - Trích xuất thành công đặc trưng tần suất và ngữ cảnh trích dẫn từ cây M2.
  - Hàm đánh giá MRR@5 kiểm thử đạt 100% tính đúng đắn trên mọi góc biên.
  - Hoàn thiện tệp PowerPoint thiết kế Poster khổ A2 dọc ($420 \times 594\text{ mm}$) với số liệu và đồ thị trực quan sinh động.
