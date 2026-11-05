# Phiếu Phân công Công việc Milestone 2 cho 4 Thành viên (B $\rightarrow$ E)

> **Dự án:** `arxiv-pipeline` & `arxiv-book` — Milestone 2: Phân tích Cú pháp Phân cấp, Chuẩn hóa và Khử trùng lặp LaTeX
> **Hạn nộp (Moodle):** **13/11/2026, 23:59** — repo mã + Jupyter Book (phần M2). Không nộp dữ liệu Parquet ở mốc này.
> **Căn cứ kỹ thuật:** [`milestone2-implementation-plan-vi.md`](./milestone2-implementation-plan-vi.md) và đề bài [`milestone2-parsing-vi.md`](../project%20requirements/milestone2-parsing-vi.md).
> **Mục tiêu của bản phân công:** Chia bài toán Milestone 2 thành **4 trụ cột thuật toán độc lập cho B, C, D, E** (dưới sự điều phối và ghép nối pipeline của Leader **A**), đảm bảo:
>
> 1. **Điểm ý tưởng giải quyết vấn đề cao nhất:** Mỗi thành viên từ B đến E đều nắm giữ một **giải pháp thuật toán chuyên sâu** giải quyết triệt để các góc khuất của mã nguồn LaTeX thực tế trên arXiv.
> 2. **Làm việc song song 100% (Zero-Blocking):** Nhờ khóa trước hợp đồng dữ liệu `src/parsing/models.py`, cả 4 người B, C, D, E có thể code và chạy `pytest` độc lập ngay từ ngày đầu tiên mà không phải chờ module của nhau.
> 3. **Lịch sử commit đồng đều & Sẵn sàng cho Milestone 3:** Cả 4 thành viên đều có commit `.py` + unit test trong repo chính và đóng góp trực tiếp vào cuốn Jupyter Book.

---

## 1. Bảng Tổng quan Phân vai & Ma trận Review Chéo

| Thành viên                  | Trụ cột phụ trách (Role)                                                                                      | Module / File được phép sửa                                                                                                                                                                    | Ý tưởng giải quyết vấn đề cốt lõi (Điểm nhấn chấm bài)                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | Người Review PR |
| :-------------------------- | :------------------------------------------------------------------------------------------------------------ | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :-------------- |
| **C - Quỳnh**               | **Trụ cột 1: Đa tệp & Cây phân cấp**<br/>_(Multi-file & Hierarchy AST)_                                       | `src/parsing/gathering.py`<br/>`src/parsing/hierarchy.py`<br/>`src/tests/test_gathering.py`<br/>`src/tests/test_hierarchy.py`                                                                  | 1. **Multi-Signal Root Election & Comment-Aware DFS:** Khử `%` comment trước khi quét, chấm điểm chọn đúng file gốc `\documentclass` (loại `standalone`/nháp), nối phẳng `\input`/`\include` có chống đệ quy vòng.<br/>2. **Dynamic Monotonic Rank Stack:** Tự động thích ứng mọi cấu trúc đề mục nhảy cấp; giữ `\section*{Acknowledgements/Appendix}`, cắt bỏ hoàn toàn mục `References` khỏi cây.                                                                                                                      | **C**           |
| **B - Sơn**                 | **Trụ cột 2: Làm sạch, Chuẩn hóa Toán & Tách Câu**<br/>_(Cleanup, Math & Sentence Tokenizer)_                 | `src/parsing/cleaner.py`<br/>`src/parsing/math_normalizer.py`<br/>`src/parsing/sentence_splitter.py`<br/>`src/tests/test_cleaner_math.py`<br/>`src/tests/test_sentence_splitter.py`            | 1. **Safe Preamble Macro Expander:** Giải nén các macro `\newcommand`/`\def` (0–3 tham số) của tác giả trước khi parse.<br/>2. **State-Machine Math Scanner:** Phân biệt `\$` (tiền tệ), `$...$` và `$$...$$`, đồng nhất mọi môi trường toán khối về `\begin{equation}`.<br/>3. **Protect–Mask–Split–Restore Tokenizer:** Che chắn toán, lệnh `\cite`/`\ref`, URL, số thập phân và >60 từ viết tắt (`et al.`, `i.e.`, `Fig. 1`, `A. Einstein`) để tách câu lá với **0% lỗi cắt nhầm**.                                   | **B**           |
| **D - Thụ**                 | **Trụ cột 3: Trích xuất BibTeX, Khử trùng 2 tầng & Xuất Parquet**<br/>_(BibTeX, Dual Dedup & Sharded Export)_ | `src/parsing/bib_extractor.py`<br/>`src/parsing/dedup.py`<br/>`src/parsing/export_m2.py`<br/>`src/tests/test_bib_extractor.py`<br/>`src/tests/test_dedup.py`<br/>`src/tests/test_export_m2.py` | 1. **Anchor-First + 4-Template Cascade `\bibitem` Parser:** Bắt mỏ neo cứng (`DOI`, `URL`, `arXiv ID`, `Year`, thẻ `\bibinfo` của RevTeX) + 4 mẫu regex chuyển `\bibitem` tự do sang BibTeX chuẩn.<br/>2. **Thứ tự vàng Dual Dedup (Union-Find Tham chiếu $\rightarrow$ Đổi tên `\cite{}` $\rightarrow$ Hash Pool Toàn văn):** Chống gộp nhầm khóa số `\bibitem{1}` khi v2 chèn thêm bài, hợp nhất trường (field union), viết lại `\cite{}` trước khi băm câu để tăng tối đa tỷ lệ gộp phần tử trùng giữa các phiên bản. | **D**           |
| **E - Triệu**               | **Trụ cột 4: Kiểm chứng Đồ thị Cây, Thống kê & Jupyter Book M2**<br/>_(Graph Validator, Analytics & Book)_    | `src/validate_m2.py`<br/>`src/tests/test_validate_m2.py`<br/>Repo sách `arxiv-book/m2/*`                                                                                                       | 1. **Graph-Theoretic & Relational Validator (`validate_m2.py`):** Kiểm chứng 8 bất biến toán học trên toàn bộ kho dữ liệu (DAG không chu trình, toàn vẹn khóa ngoại `hierarchy -> elements`, 0 rò rỉ `References`, bảo toàn phiên bản rỗng `no_source`).<br/>2. **Compile-time Visual Analytics (`arxiv-book/m2/`):** Hệ thống biểu đồ động phân tích cấu trúc cây, tỷ lệ nén khử trùng lặp theo số phiên bản $v_1 \dots v_N$, độ phủ trường BibTeX và benchmark Colab CPU.                                              | **E**           |
| **A - Phúc**<br/>_(Leader)_ | **Điều phối, Hợp đồng dữ liệu & Pipeline Đa nhân**<br/>_(Contract, Orchestrator & Profiler)_                  | `src/parsing/models.py`<br/>`src/parsing/pipeline.py`<br/>`src/run_parse.py`<br/>`src/colab_parse.ipynb`<br/>`src/tests/test_parse_pipeline.py`                                                | Khóa hợp đồng `parsing/models.py`; ghép luồng `C -> B -> D -> E`; xử lý song song `ProcessPoolExecutor` + xả **Streaming Sharded Parquet** mỗi 500 bài giữ RAM đỉnh < 450 MB trên Colab CPU.                                                                                                                                                                                                                                                                                                                             | **A**           |

---

## 2. Hợp đồng Giao tiếp Chung (`src/parsing/models.py`) — Điểm Neo Để B $\rightarrow$ E Code Song Song

Trước khi B, C, D, E mở nhánh, Leader A khởi tạo `src/parsing/models.py` với các dataclass giao tiếp sau (không ai phải đoán kiểu dữ liệu của người khác):

```python
from dataclasses import dataclass, field


@dataclass
class CompilationUnit:
    """Đầu ra của C (gathering.py) -> Đầu vào cho C (hierarchy.py), B và D."""

    arxiv_id: str
    version: int
    root_relpath: str | None  # None nếu phiên bản rỗng (no_source / ok_no_tex)
    merged_tex: str  # Nội dung LaTeX đã khử comment % và nối phẳng \input/\include
    bib_files: dict[str, str]  # {relpath: decoded_text} của các file .bib
    raw_bibitems_blocks: list[
        str
    ]  # Các khối \begin{thebibliography}...\end{thebibliography} tách ra
    total_tex_files: int = 0
    used_tex_files: int = 0
    unused_tex_files: int = 0


@dataclass
class RawNode:
    """Nút cây thô của 1 phiên bản do C (hierarchy.py) tạo và B làm sạch/tách câu."""

    node_id: int  # ID cục bộ trong phạm vi 1 phiên bản (0 = document root)
    parent_node_id: int | None  # None với nút gốc document
    kind: str  # 'document' | 'heading' | 'paragraph' | 'list' | 'item' | 'sentence' | 'block_formula' | 'figure'
    content: str  # Nội dung đã làm sạch (sau khi qua module của B)


@dataclass
class RawBibEntry:
    """Mục tham chiếu trích xuất từ 1 phiên bản (từ .bib hoặc \bibitem) do D tạo."""

    version: int
    citation_key: str
    entry_type: str  # 'article', 'inproceedings', 'book', 'misc', ...
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    doi: str = ""
    journal: str = ""
    url: str = ""
    source_kind: str = "bib"  # 'bib' hoặc 'bibitem'


@dataclass
class ElementRow:
    """Dòng xuất ra bảng `elements` (sau khi D khử trùng lặp xuyên phiên bản)."""

    elementid: str  # vd: "2408-11045#s1"
    arxivid: str  # vd: "2408.11045"
    kind: str
    content: str


@dataclass
class HierarchyRow:
    """Dòng xuất ra bảng `hierarchy`."""

    arxivid: str
    version: int
    elementid: str
    parentid: str


@dataclass
class BibtexRow:
    """Dòng xuất ra bảng `bibtex` (sau khi D khử trùng lặp & hợp nhất trường)."""

    arxivid: str
    citationkey: str
    entrytype: str
    title: str
    authors: list[str]
    year: int | None
    doi: str
    journal: str
    url: str
```

---

## 3. Đặc tả Chi tiết Từng Phần Việc cho 4 Thành viên (B $\rightarrow$ E)

---

### 👤 THÀNH VIÊN C — Trụ cột 1: Thu thập Đa tệp & Xây dựng Cây Phân cấp (`gathering.py`, `hierarchy.py`)

#### 1. Mục tiêu & Tầm quan trọng

Bạn chịu trách nhiệm ở **cửa ngõ đầu tiên của Parser**: nhận tập file `.tex`/`.bib` thô từ một phiên bản bài báo, xác định chính xác những file nào thực sự được biên dịch ra PDF, nối chúng lại thành một dòng chảy LaTeX duy nhất, và dựng lên bộ khung cây phân cấp (`Document` $\rightarrow$ `Heading` $\rightarrow$ `Paragraph` / `List` $\rightarrow$ `Item` / `Block Formula` / `Figure`).

#### 2. Các nhiệm vụ cụ thể (Mã task: `M2-C1`, `M2-C2`, `M2-C3`)

- **`M2-C1` — `src/parsing/gathering.py` (Thu thập đa tệp & Khử comment):**
  - Viết hàm `strip_comments(tex: str) -> str`:
    - Xóa mọi comment bắt đầu bằng `%` không bị escape (`\%` phải giữ nguyên!).
    - **Lưu ý đặc biệt:** Không xóa `%` nằm trong môi trường `\begin{verbatim}...\end{verbatim}`, `\begin{lstlisting}...\end{lstlisting}` hoặc `\verb|...|`, và giữ `%` trong URL nếu nằm trong `\url{...}`.
    - Khi một dòng kết thúc bằng `%` (dùng để nối dòng trong LaTeX), xóa `%` và nối sát dòng kế tiếp mà không chèn dấu cách thừa.
  - Viết hàm `elect_root_tex(tex_files: dict[str, str]) -> str | None`:
    - Quét trên văn bản **đã xóa comment** để tìm các file chứa `\documentclass` (hoặc `\documentstyle`).
    - Giải quyết bài toán **nhiều file có `\documentclass`** (ví dụ có thêm file `supplement.tex`, `rebuttal.tex`, hoặc hình vẽ `\documentclass{standalone}`): dùng hàm chấm điểm đa tín hiệu (cộng điểm cho file có `\begin{document}`, trừ điểm nếu là `standalone`/`subfiles`, cộng điểm theo số lệnh `\input`/`\include` bên trong, cộng điểm nếu tên file là `main.tex`, `paper.tex`, `ms.tex`, `article.tex`).
    - Nếu không file nào có `\documentclass`, fallback chọn file có `\begin{document}` hoặc file `.tex` chính lớn nhất.
  - Viết hàm `gather_compilation_unit(arxiv_id: str, version: int, raw_files: dict[str, bytes]) -> CompilationUnit`:
    - Giải mã bytes an toàn (`utf-8-sig`, fallback `latin-1`).
    - Duyệt đệ quy DFS từ `root_tex` để thay thế tại chỗ các lệnh `\input{...}`, `\input ...`, `\include{...}`, `\subfile{...}`, `\import{dir}{file}`.
    - Tự động thêm đuôi `.tex` nếu lệnh viết `\input{sections/intro}`; thử tìm đường dẫn tương đối tính từ thư mục của `root_tex` trước, nếu không thấy thì tìm tương đối từ thư mục của file hiện tại.
    - **Chống đệ quy vô hạn (Cycle Detection):** Dùng tập `active_stack` để phát hiện chu trình $A \to B \to A$ và giới hạn độ sâu $\le 15$.
    - Đếm chính xác `total_tex_files`, `used_tex_files`, `unused_tex_files` để đưa vào báo cáo.

- **`M2-C2` — `src/parsing/hierarchy.py` (Xây dựng cây phân cấp thích ứng):**
  - Viết hàm `build_raw_hierarchy(unit: CompilationUnit, cleaner_hooks) -> tuple[list[RawNode], list[str]]`:
    - Nếu `unit.root_relpath is None` (phiên bản `no_source` / `ok_no_tex`), trả về danh sách rỗng `[]` (không báo lỗi).
    - Tạo nút gốc `RawNode(node_id=0, parent_node_id=None, kind="document", content=f"Version {unit.version} root")`.
    - Cắt lấy phần thân bên trong `\begin{document} ... \end{document}` (phần preamble trước `\begin{document}` chuyển cho hàm mở rộng macro của **B**).
    - **Ngăn xếp Cấp bậc Động (Dynamic Monotonic Rank Stack):**
      - Hỗ trợ đầy đủ các lệnh phân mục (cả có đánh số và có dấu `*` như `\section*{...}`, lẫn tùy chọn tiêu đề ngắn `\section[short]{long}`):
        `part (0) < chapter (1) < section (2) < subsection (3) < subsubsection (4) < paragraph (5) < subparagraph (6)`.
      - Khi gặp lệnh `\appendix` hoặc `\begin{appendices}`, đánh dấu vùng phụ lục (vẫn giữ nguyên trong cây theo đúng yêu cầu đề).
      - **Quy tắc Loại trừ / Bao gồm nghiêm ngặt của đề bài:**
        - Nếu tiêu đề mục (sau khi bỏ lệnh định dạng, đổi chữ thường) khớp với `references`, `bibliography`, `literature cited`, `works cited` HOẶC gặp môi trường `\begin{thebibliography}...\end{thebibliography}`: **Tách khối đó đưa vào `raw_bibitems_blocks` cho D**, **tuyệt đối không đưa vào cây phân cấp**.
        - Nếu tiêu đề mục là `acknowledgements`, `acknowledgments`, `appendix`... (dù viết bằng `\section*`): **Bắt buộc đưa vào cây phân cấp**.
    - **Phân nhánh Danh sách (`itemize`, `enumerate`, `description`):**
      - Khối `\begin{itemize}...\end{itemize}` tạo nút `kind="list"`.
      - Mỗi `\item` bên trong tạo nút con `kind="item"` gắn vào nút `list` đó (hỗ trợ danh sách lồng nhau nhiều cấp).
    - **Tách các khối Lá Đặc biệt (`figure` và `block_formula`) & Đoạn văn (`paragraph`):**
      - Khối `\begin{figure[*]}...`, `\begin{table[*]}...`, `\begin{wrapfigure}...`, `\begin{algorithm[*]}...` $\rightarrow$ tạo nút lá `kind="figure"` (đề bài quy định Tables cũng thuộc loại Figure).
      - Khối `\begin{equation}...` (sau chuẩn hóa của B) $\rightarrow$ tạo nút lá `kind="block_formula"`.
      - Văn xuôi còn lại chia theo đoạn trống (`\n\s*\n` hoặc `\par`) thành nút `kind="paragraph"`, và gọi hàm tách câu của **B** để sinh các nút lá `kind="sentence"`.

- **`M2-C3` — Bộ Unit Test (`src/tests/test_gathering.py`, `src/tests/test_hierarchy.py`) & Mục 1 Báo cáo (`m2/01_parsing_hierarchy.md`):**
  - Ít nhất **25 unit tests** kiểm tra: comment `% \documentclass`, chọn root giữa `main.tex` và `fig_standalone.tex`, nối `\input` 3 tầng lồng nhau, chống chu trình $A \to B \to A$, bỏ file rác `unused.tex`, cây nhảy cấp từ `\section` xuống `\paragraph`, `itemize` lồng nhau 2 cấp, giữ `\section*{Acknowledgements}`, loại bỏ `\section*{References}` và `\begin{thebibliography}`.
  - Viết phần giải thích thuật toán Multi-file Gathering & Monotonic Stack Hierarchy trong `m2/01_parsing_hierarchy.md`.

- **Điều kiện hoàn thành (Done when):** `pytest src/tests/test_gathering.py src/tests/test_hierarchy.py` xanh 100%, `ruff check` sạch, chạy thử trên 10 bài báo thật trong `data/papers/` không văng bất kỳ ngoại lệ nào.

---

### 👤 THÀNH VIÊN B — Trụ cột 2: Làm sạch LaTeX, Chuẩn hóa Toán & Tách Câu Ngữ cảnh (`cleaner.py`, `math_normalizer.py`, `sentence_splitter.py`)

#### 1. Mục tiêu & Tầm quan trọng

Bạn phụ trách **chất lượng ngữ nghĩa của mọi nút trong cây**: biến mã LaTeX thô đầy rác trình bày và macro tự chế thành văn bản học thuật sạch sẽ, toán học đồng nhất 100%, và tách câu lá chính xác tuyệt đối không cắt nhầm vào công thức toán hay từ viết tắt. Chất lượng của module B quyết định trực tiếp **tỷ lệ khớp toàn văn (Full-text Deduplication)** ở bước sau!

#### 2. Các nhiệm vụ cụ thể (Mã task: `M2-B1`, `M2-B2`, `M2-B3`, `M2-B4`)

- **`M2-B1` — `src/parsing/cleaner.py` (Mở rộng Macro Preamble & Dọn rác LaTeX):**
  - Viết hàm `extract_and_expand_macros(preamble: str, body: str) -> str`:
    - Bắt các định nghĩa `\newcommand{\name}[nargs]{def}`, `\renewcommand`, `\providecommand` ($0 \le \text{nargs} \le 3$) và `\def\name{def}` đơn giản trong `preamble`.
    - Không mở rộng các lệnh cấu trúc hệ thống (`\section`, `\item`, `\begin`, `\end`, `\cite`, `\ref`, `\label`).
    - Mở rộng an toàn trên `body` với giới hạn tối đa 5 vòng lặp để chống macro đệ quy.
  - Viết hàm `clean_latex_content(text: str, is_figure: bool = False) -> str`:
    - Xóa bỏ các tùy chọn vị trí float `[htbp!]`, `[H]`, `[!t]` ngay sau `\begin{figure}` / `\begin{table}`.
    - Xóa các lệnh trình bày không mang ngữ nghĩa: `\centering`, `\raggedright`, `\raggedleft`, `\noindent`, `\indent`, `\toprule`, `\midrule`, `\bottomrule`, `\hline`, `\cline{...}`, `\vspace[*]{...}`, `\hspace[*]{...}`, `\smallskip`, `\medskip`, `\bigskip`, `\newpage`, `\clearpage`, `\pagebreak`, `\allowbreak`, `\sloppy`, `\fussy`, các lệnh đổi cỡ chữ `\small`, `\footnotesize`, `\scriptsize`, `\tiny`, `\large`, `\Large`, `\LARGE`, `\huge`.
    - Bóc vỏ các lệnh định dạng chữ trong văn xuôi (giữ lại phần chữ bên trong): `\textbf{...}`, `\textit{...}`, `\emph{...}`, `\underline{...}`, `\textrm{...}`, `\textsf{...}`, `\texttt{...}`, `\textsc{...}` (hỗ trợ ngoặc nhọn lồng nhau).
    - Chuẩn hóa các biến thể trích dẫn (`\citep{...}`, `\citet{...}`, `\parencite{...}`, `\textcite{...}`) và chuẩn hóa danh sách khóa bên trong `\cite{k1, k2}` để module của **D** dễ dàng đổi tên khóa.
    - Thu gọn khoảng trắng thừa (`[ \t]+` $\rightarrow$ `' '`) để hai phiên bản chỉ lệch nhau dấu cách/xuống dòng sẽ có chuỗi `content` giống hệt nhau.

- **`M2-B2` — `src/parsing/math_normalizer.py` (Chuẩn hóa Toán Nội dòng & Toán Khối):**
  - Viết hàm `normalize_math(text: str) -> str`:
    - Bảo vệ ký tự tiền tệ `\$` trước khi phân tích.
    - **Toán nội dòng (Inline Math):** Chuyển mọi `\(...\)`, `\begin{math}...\end{math}`, `\ensuremath{...}` về dạng chuẩn `$...$`; xóa khoảng trắng thừa sát bên trong dấu `$` (ví dụ `$ x + y $` $\rightarrow$ `$x + y$`).
    - **Toán khối (Block Math):** Chuyển mọi khối `$$...$$`, `\[...\]`, `\begin{displaymath}...\end{displaymath}`, `\begin{equation*}...\end{equation*}`, `\begin{align[*]}...\end{align[*]}`, `\begin{gather[*]}...\end{gather[*]}`, `\begin{multline[*]}...\end{multline[*]}`, `\begin{eqnarray[*]}...\end{eqnarray[*]}` về dạng chuẩn duy nhất:
      `\begin{equation}...\end{equation}`.
    - Bên trong khối phương trình: xóa `\nonumber`, `\notag`, `\label{...}` để chuẩn hóa nội dung toán.

- **`M2-B3` — `src/parsing/sentence_splitter.py` (Tách Câu Ngữ cảnh Protect–Mask–Split–Restore):**
  - Viết hàm `split_sentences(paragraph_text: str) -> list[str]`:
    - **Cấm tuyệt đối tách câu tại:**
      1. Dấu chấm bên trong công thức toán `$...$`.
      2. Dấu chấm bên trong các lệnh LaTeX (`\cite{...}`, `\ref{fig.1}`, `\url{...}`, `\href{...}{...}`).
      3. Các từ viết tắt học thuật: `i.e.`, `e.g.`, `et al.`, `cf.`, `vs.`, `w.r.t.`, `Fig.`, `Figs.`, `Eq.`, `Eqs.`, `Sec.`, `Secs.`, `Tab.`, `Tabs.`, `Alg.`, `Thm.`, `Lem.`, `Prop.`, `Cor.`, `Def.`, `Ref.`, `Refs.`, `Ch.`, `App.`, `Vol.`, `No.`, `pp.`, `Dr.`, `Prof.`, `Mr.`, `Mrs.`, `Inc.`, `Ltd.`, `U.S.`, `Ph.D.`...
      4. Chữ cái viết tắt tên người: `A. Einstein`, `J. K. Rowling`.
      5. Số thập phân (`3.14159`, `p < 0.05`) và địa chỉ URL (`https://arxiv.org/abs/2408.11045`).
    - Thực hiện đúng 4 bước: **Mask** (thay toán/lệnh/URL/số thập phân bằng placeholder `__M0__`, `__C0__`) $\rightarrow$ **Guard** (thay dấu chấm viết tắt bằng `\uE000`) $\rightarrow$ **Split** (tách theo dấu `[.!?]+` kết câu thật) $\rightarrow$ **Restore** (khôi phục hoàn nguyên).

- **`M2-B4` — Bộ Unit Test (`test_cleaner_math.py`, `test_sentence_splitter.py`) & Mục Chuẩn hóa trong Báo cáo (`m2/02_standardization_dedup.md`):**
  - Ít nhất **30 unit tests** bao phủ mọi trường hợp biên của toán, macro, lệnh định dạng và câu chứa hỗn hợp công thức + từ viết tắt + trích dẫn.
  - Viết nửa đầu chương `m2/02_standardization_dedup.md` (trình bày giải pháp Macro Expansion, State-Machine Math Normalization và Protect–Mask–Split–Restore Sentence Splitting).

- **Điều kiện hoàn thành (Done when):** Toàn bộ test của B xanh 100%, không làm hỏng bất kỳ lệnh `\cite{...}` nào, và tốc độ xử lý đạt $\ge 5 \text{ MB văn bản/giây}$ trên 1 nhân CPU.

---

### 👤 THÀNH VIÊN D — Trụ cột 3: Trích xuất BibTeX, Khử trùng lặp 2 Tầng & Xuất Sharded Parquet (`bib_extractor.py`, `dedup.py`, `export_m2.py`)

#### 1. Mục tiêu & Tầm quan trọng

Bạn nắm giữ **phần thuật toán phức tạp nhất về mặt cấu trúc dữ liệu liên phiên bản**: trích xuất toàn bộ tài liệu tham khảo từ cả `.bib` và `\bibitem`, khử trùng lặp ở cả **2 mức** (mức tham chiếu kèm hợp nhất trường + đổi tên `\cite{}`, và mức nội dung toàn văn xuyên phiên bản), sau đó đóng gói thành 3 bảng Parquet chuẩn cho Milestone 2 & Milestone 3.

#### 2. Các nhiệm vụ cụ thể (Mã task: `M2-D1`, `M2-D2`, `M2-D3`, `M2-D4`)

- **`M2-D1` — `src/parsing/bib_extractor.py` (Trích xuất `.bib` và Chuyển đổi `\bibitem` $\rightarrow$ BibTeX):**
  - Viết hàm `parse_bib_file(bib_content: str, version: int) -> list[RawBibEntry]`:
    - Bộ phân tích `.bib` đếm ngoặc nhọn cân bằng `{...}`, giải quyết `@string{...}`, trích xuất chính xác `entrytype`, `citationkey`, `title`, `authors` (tách theo `and`), `year` (`int | None`), `doi`, `journal` (fallback sang `booktitle`/`eprint`), `url` (nếu có `eprint`/`arxivid` mà chưa có `url`, tự sinh `https://arxiv.org/abs/<id>`).
  - Viết hàm `parse_bibitems(thebibliography_blocks: list[str], version: int) -> list[RawBibEntry]`:
    - Tách từng mục `\bibitem[optional]{key} ...`.
    - Áp dụng **Anchor-First + 4-Template Cascade Regex**:
      1. Bắt các mỏ neo cứng trước: `DOI`, `URL`, `arXiv ID`, `Year`, và các thẻ `\bibinfo{author}{...}`, `\bibinfo{title}{...}`, `\bibinfo{journal}{...}` (do BibTeX/RevTeX sinh ra).
      2. Áp dụng 4 mẫu phân tách `authors`, `title`, `journal` cho văn bản tự do:
         - Mẫu 1: Tiêu đề nằm trong ngoặc kép ` ``...'' ` hoặc `"..."` hoặc `\enquote{...}`.
         - Mẫu 2: Tiêu đề hoặc Tạp chí nằm trong `\emph{...}` / `\textit{...}` (phân biệt tạp chí hay tiêu đề dựa vào từ khóa `Phys. Rev.`, `IEEE`, `Proc.`, `J.`, `Vol.` hoặc số tập `\textbf{\d+}`).
         - Mẫu 3: Phân đoạn theo dấu chấm `. ` (Authors. Title. Venue, Year).
         - Mẫu 4: Định dạng Preprint arXiv (`Authors, Title, arXiv:YYMM.NNNNN`).

- **`M2-D2` — `src/parsing/dedup.py` (Khử trùng lặp 2 Tầng Xuyên Phiên bản):**
  - **Tầng 1 — `deduplicate_references(entries: list[RawBibEntry]) -> tuple[list[BibtexRow], dict[tuple[int, str], str]]`:**
    - Gom cụm các `RawBibEntry` của cùng một bài báo xuyên suốt mọi phiên bản bằng cấu trúc **Union-Find**:
      - Nối cạnh nếu: (a) cùng `DOI` khác rỗng, HOẶC (b) cùng `arXiv ID` khác rỗng, HOẶC (c) tiêu đề sau chuẩn hóa khớp chính xác / tương đồng $\ge 0.85$ kèm cùng năm hoặc cùng họ tác giả đầu, HOẶC (d) cùng `citation_key` giữa các phiên bản **và không bị xung đột nội dung** (chống bẫy khóa số `\bibitem{1}` bị đổi bài khi tác giả thêm trích dẫn mới ở v2!).
    - Với mỗi cụm:
      - Chọn 1 `canonical_key` duy nhất (tất định).
      - **Hợp nhất các trường (Field Union):** Lấy hợp thông tin đầy đủ nhất của `title`, `authors`, `year`, `doi`, `journal`, `url`, `entrytype` từ tất cả các bản ghi trong cụm (đúng ví dụ `vaswani2017` + `vit_transformer` trong đề bài!).
      - Sinh từ điển ánh xạ `cite_rename_map[(version, old_key)] = canonical_key`.
  - **Tầng 2 — `rewrite_citations_and_dedup_elements(arxiv_id: str, version_trees: dict[int, list[RawNode]], cite_rename_map: dict[tuple[int, str], str]) -> tuple[list[ElementRow], list[HierarchyRow]]`:**
    - **Bước 2a (Đổi tên `\cite{}` TRƯỚC):** Duyệt mọi nút trong `version_trees[v]`, thay mọi khóa cũ trong `\cite{k1, k2}` thành `canonical_key` theo `cite_rename_map[(v, k)]`, khử khóa trùng trong cùng một `\cite`.
    - **Bước 2b (Khử trùng lặp Toàn văn SAU):** Dùng bảng băm `pool[(node.kind, node.content)] -> elementid` xuyên suốt $v = 1 \dots N$:
      - Nút gốc `document` của phiên bản $v$ luôn có `elementid = f"{dirname}#root{v}"`.
      - Mọi nút khác (`heading`, `paragraph`, `list`, `item`, `sentence`, `block_formula`, `figure`): nếu `(kind, content)` đã có trong `pool` thì **dùng lại `elementid` cũ**; nếu chưa có thì cấp `elementid` mới (`#h1`, `#p1`, `#s1`, `#eq1`, `#fig1`...) và thêm vào `elements`.
      - Sinh các cạnh `HierarchyRow(arxivid, version=v, elementid=child_id, parentid=parent_id)`.

- **`M2-D3` — `src/parsing/export_m2.py` (Streaming Sharded Parquet Exporter & Thống kê):**
  - Viết class `ShardedParquetExporter`:
    - Nhận luồng kết quả từng bài báo, gom đủ lô (mặc định 500 bài) thì ghi ra các shard `elements/part-000XX.parquet`, `hierarchy/part-000XX.parquet`, `bibtex/part-000XX.parquet` với `pyarrow` schema chuẩn (`pa.dictionary` cho `kind` và `entrytype`).
    - Tính và xuất các file thống kê gọn nhẹ vào `data/stats/` (`m2_summary.json`, `m2_papers_stats.csv`, `m2_elements_kind.csv`) để Jupyter Book biên dịch biểu đồ.

- **`M2-D4` — Bộ Unit Test (`test_bib_extractor.py`, `test_dedup.py`, `test_export_m2.py`) & Nửa sau Chương 2 Báo cáo (`m2/02_standardization_dedup.md`):**
  - Kiểm thử trực tiếp kịch bản trong đề bài (`vaswani2017` ở v1 $\rightarrow$ `vit_transformer` thêm `doi` ở v2 $\rightarrow$ `vit_transformer` mất `year` ở v3 $\rightarrow$ kết quả hợp nhất đủ cả `title, author, year, doi` và câu chứa `\cite` ở v1, v2 gộp thành 1 dòng `elements` duy nhất!).
  - Viết phần Trích xuất Tham chiếu & Khử trùng lặp 2 tầng trong `m2/02_standardization_dedup.md`.

- **Điều kiện hoàn thành (Done when):** Toàn bộ test của D xanh 100%, 3 bảng Parquet xuất ra đúng tên cột & kiểu dữ liệu PyArrow theo đề bài.

---

### 👤 THÀNH VIÊN E — Trụ cột 4: Kiểm chứng Đồ thị Cây, Phân tích Thống kê & Jupyter Book Phần 2 (`validate_m2.py`, `arxiv-book/m2/*`)

#### 1. Mục tiêu & Tầm quan trọng

Bạn là **chốt chặn bảo đảm chất lượng (QA Gatekeeper)** và là **kiến trúc sư báo cáo khoa học của nhóm**: viết công cụ kiểm chứng toán học cho toàn bộ 3 bảng dữ liệu M2 (`validate_m2.py`), và xây dựng Phần 2 của cuốn sách Jupyter Book (`arxiv-book/m2/`) với các biểu đồ tương tác sinh tự động lúc build sách.

#### 2. Các nhiệm vụ cụ thể (Mã task: `M2-E1`, `M2-E2`, `M2-E3`)

- **`M2-E1` — `src/validate_m2.py` & `src/tests/test_validate_m2.py` (Kiểm chứng 8 Bất biến Dữ liệu M2):**
  - Xây dựng CLI & thư viện `validate_m2.py` kiểm tra tự động:
    1. **Schema & Non-null Check:** Đọc các shard Parquet của `elements`, `hierarchy`, `bibtex` bằng `pyarrow`, xác nhận đúng tên cột, đúng kiểu `dictionary`/`list<string>`/`int32`, không có giá trị `null` trái phép.
    2. **Referential Integrity Check:** Mọi `elementid` và `parentid` trong `hierarchy` đều tồn tại trong `elements` của cùng `arxivid`.
    3. **Tree / DAG Structural Check:** Với mỗi `(arxivid, version)`, đồ thị `elementid -> parentid` có đúng 1 gốc `#root{version}`, liên thông tới gốc và **không có chu trình**.
    4. **Deduplication Check:** Trong mỗi `arxivid`, không có 2 dòng `elements` (khác `document`) trùng cả `(kind, content)`; không có 2 dòng `bibtex` trùng `citationkey`.
    5. **Zero Reference Leakage Check:** Không còn mục `References` hay `\bibitem` lọt vào bảng `elements`.
    6. **Version Coverage Check:** Đối chiếu với `Corpus` của Milestone 1, đảm bảo mọi phiên bản (kể cả `no_source` / `ok_no_tex`) đều được tính đúng trong báo cáo độ bao phủ.
  - Viết `src/tests/test_validate_m2.py` kiểm tra cả dữ liệu hợp lệ (PASS) và dữ liệu cố tình làm lỗi như cây có chu trình, cạnh mồ côi, trùng phần tử (phải bắt được lỗi).

- **`M2-E2` — Chương 3 & Chương 4 Jupyter Book M2 (`m2/03_parsing_statistics.ipynb`, `m2/04_parser_performance.ipynb`):**
  - Cập nhật `_toc.yml` của `arxiv-book` để thêm Phần 2 (Milestone 2) ngay bên dưới Milestone 1 (giữ nguyên toàn bộ M1 theo đúng yêu cầu đề bài: _"thêm một phần về phân tích cú pháp và chuẩn hóa vào cuốn sách đó thay vì tạo một cuốn mới"_).
  - Trong `m2/03_parsing_statistics.ipynb` (sinh hình/bảng trực tiếp khi build sách):
    - Bảng & biểu đồ tỷ lệ phân tích thành công, độ bao phủ phiên bản trên từng bài báo (bao gồm tỷ lệ phiên bản rỗng không có TeX), hiệu quả lọc bỏ tệp `.tex` dư thừa.
    - Phân bố các loại phần tử (`sentence`, `heading`, `paragraph`, `block_formula`, `figure`, `list`, `item`) và phân phối độ sâu cây phân cấp.
    - Biểu đồ **Trước vs. Sau Khử trùng lặp** cho cả `elements` (theo số phiên bản $N=1, 2, 3, \ge 4$) và `bibtex` (kèm tỷ lệ lấp đầy trường nhờ phép hợp nhất `Field Union`).
  - Trong `m2/04_parser_performance.ipynb`:
    - Biểu đồ phân rã thời gian chạy (Wall time end-to-end & trung bình mỗi bài báo theo từng bước xử lý) và biểu đồ bộ nhớ RAM đỉnh / dung lượng lưu trữ Parquet trên Google Colab CPU.

- **`M2-E3` — Đồng bộ Giao diện & Kiểm tra Build Sách Tự động (CI GitHub Pages):**
  - Ghép nội dung lý thuyết từ C và B/D vào `m2/01_parsing_hierarchy.md` và `m2/02_standardization_dedup.md`, thêm sơ đồ trực quan (Mermaid / HTML-SVG cùng phong cách học thuật với M1).
  - Đảm bảo `jupyter-book build . -W` chạy thành công **0 cảnh báo (0 warnings)** và tự động deploy lên GitHub Pages.

- **Điều kiện hoàn thành (Done when):** `pytest src/tests/test_validate_m2.py` xanh 100%, `validate_m2.py` chạy PASS trên dữ liệu thật, và `arxiv-book` build sạch cả Phần M1 lẫn Phần M2 trên GitHub Pages.

---

## 4. Lộ trình triển khai & phối hợp (3 phase)

**Hạn cứng:** **13/11/2026, 23:59** (Moodle: link repo mã + Jupyter Book có phần M2).
**Mục tiêu nộp:** mã chạy được, sách build sạch, số liệu từ parse thật trên corpus đã crawl — **không** đẩy Parquet lên Moodle.

### Nhìn một lần

```
06/11  Phase 0   A khóa models.py → B,C,D,E bắt đầu song song
06–12/11  Phase 1   B,C,D,E: module + pytest (PR merge dần)
13/11           A: ghép pipeline + pilot ~100 bài -> Parse 25k · validate · stats → arxiv-book · đóng băng
```

### Bảng phase (khớp 3 dòng trên)

| Phase                         | Thời gian           | Việc chính                                                                                                                                                                                                                                                                                                            | Owner                                                                                                                                                       | Xong khi                                                             |
| ----------------------------- | ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| **0 — Khởi tạo**              | **06/11**           | Khóa `src/parsing/models.py`, khung `src/parsing/`, nhánh `m2/...`                                                                                                                                                                                                                                                    | **A**                                                                                                                                                       | Trong ngày 06/11: B–E mở nhánh, test giả chạy được                   |
| **1 — Module song song**      | **06–12/11**        | **C:** `gathering` + `hierarchy` · **B:** `cleaner` + `math` + `sentence_splitter` · **D:** `bib_extractor` + `dedup` + `export_m2` · **E:** `validate_m2` + khung `arxiv-book/m2/` (số mẫu / placeholder)                                                                                                            | **B, C, D, E**                                                                                                                                              | Hết **12/11**: PR trụ cột merge; `pytest` + `ruff` xanh trên `main`  |
| **2 — Ghép, chạy full & nộp** | **13/11** (cả ngày) | **Sáng–trưa (A):** ghép `pipeline.py` + `run_parse.py`, pilot ~100 bài → sửa lỗi tích hợp nếu có · **Trưa–chiều (A+D):** parse 25k (Colab, `data/` local) · **Chiều (E+C):** `validate_m2.py`, đối chiếu ~20 bài · **Tối (E+D):** chép `stats/m2_*`, hoàn thiện sách, `jupyter-book build -W` · **23:59:** nộp Moodle | **A** (pipeline + parse + dán link) · **D** (export/stats) · **E** (sách) · **C** (validate mẫu) · **B** (sẵn sàng fix nhanh nếu pilot báo lỗi clean/split) | Trước **23:59 13/11**: validate PASS, book build sạch, 2 link Moodle |

### Phụ thuộc tối thiểu (tránh chờ vô ích)

1. **06/11 — Phase 0 xong trong ngày** → từ **06/11** B, C, D, E code **song song** (fixture + `models.py`, chưa cần pipeline).
2. **Hết 12/11 — module trên `main`** → **13/11 sáng** A mới ghép `pipeline.py` (thứ tự 1 bài: gathering → clean/math/split → hierarchy → bib → dedup → export).
3. **13/11 — pilot ~100 bài OK** → mới bật parse full 25k (≈ **15–25 phút** trên Colab 2 CPU nếu corpus ở `/content/data`; **không** parse trực tiếp trên Drive FUSE).
4. **Parse xong** → `validate_m2` + chép stats vào sách (E/D); chỉ đóng băng README/repo khi book build xanh.

### Phối hợp Git (ngắn)

- Nhánh: `m2/<vai>-<mô-tả>` (vd. `m2/c-gathering-hierarchy`).
- Chỉ sửa file thuộc trụ cột mục 1; đổi `models.py` → PR riêng do **A**.
- Trước PR: `ruff check . && ruff format . && pytest`.
- **M3:** D giữ `doi` / `url` (kể cả arXiv trong `\bibitem`) trong bảng `bibtex` — dùng cho matching sau này.
