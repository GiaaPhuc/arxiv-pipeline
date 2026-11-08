# Thu thập nhiều tệp và xây dựng cây phân cấp LaTeX

> Bản thảo phần C để E ghép vào `arxiv-book/m2/01_parsing_hierarchy.md`.
> Implementation: `src/parsing/gathering.py`, `src/parsing/hierarchy.py`.
> Đã kiểm thử bằng nguồn tổng hợp và thử C độc lập trên **10 bài / 26 phiên bản thật**
> với hook truyền nguyên văn. **Chưa tích hợp B/D/E thật**, chưa đo hiệu năng Colab.
> Bằng chứng và giới hạn: [kiểm chứng mẫu thật](02_c_real_sample_check.md).

## 1. Đầu vào, đầu ra và phạm vi

Mỗi phiên bản được truyền vào dưới dạng từ điển đường dẫn tương đối → bytes.
Module C không tải mạng, không thực thi TeX, không đọc các đường dẫn include
trực tiếp từ filesystem và không sửa corpus M1.

`gather_compilation_unit(arxiv_id, version, raw_files)` trả `CompilationUnit`:
file gốc, nội dung đã gom, các file BibTeX, các khối `thebibliography` và số file
TeX tổng/dùng/bỏ. `build_raw_hierarchy(unit, cleaner_hooks)` trả bộ đôi
`(list[RawNode], list[str])`; thành phần thứ hai chứa tham khảo bổ sung giao cho D.
Các ID nguyên của nút chỉ có ý nghĩa trong một phiên bản. D chịu trách nhiệm
khử trùng và cấp ID bảng đầu ra.

Phiên bản không có TeX có `root_relpath=None`; hierarchy trả `([], [])`, không
tạo một gốc giả. Gathering vẫn giữ `.bib` và các khối tham khảo `.bbl` được cung
cấp để không mất thông tin có thể chuyển sang D.

## 2. Gom nguồn theo đường biên dịch

### 2.1. Giải mã và khử comment

Bytes được giải mã bằng `utf-8-sig`; nếu không hợp lệ, dùng `latin-1`.
Bộ quét phân biệt lệnh TeX và vùng literal. `%` không bị escape bắt đầu comment;
số dấu backslash liên tiếp quyết định `%` có được escape hay không. Comment và
newline sau nó được bỏ, không chèn dấu cách giả vào vị trí nối dòng.

Giữ nguyên `%` trong `verbatim`, `verbatim*`, `lstlisting`, `\verb`, `\verb*`,
`\url{...}` và `\path{...}`. Các lệnh giả như `\input{draft}` trong ví dụ mã
không được thực hiện. Các argument được đọc bằng ngoặc cân bằng, hỗ trợ ngoặc
lồng nhau và ký tự escape; không dùng một regex lớn để parse toàn bộ LaTeX.

### 2.2. Chọn file gốc có tính tất định

Ưu tiên tập ứng viên có `\documentclass` hoặc `\documentstyle` ngoài vùng
comment/literal. Điểm số theo kế hoạch leader, điều chỉnh tín hiệu include sau
kiểm chứng nguồn thật để không thưởng đường dẫn thiếu:

```text
100 × có begin{document}
−80 × class standalone hoặc subfiles
+25 × số lệnh nạp có đích tìm được trong mapping từ thư mục root ứng viên
+20 × tên main/paper/ms/article/root.tex
+15 × có maketitle hoặc abstract
+min(10, độ dài văn bản / 10000)
```

Nếu hòa điểm, chọn đường dẫn theo thứ tự từ điển. Nếu không có khai báo class,
ưu tiên file có `begin{document}`; nếu cũng không có thì chọn file TeX lớn nhất.
Đây là heuristic; bài có nhiều root độc lập vẫn cần được kiểm chứng trên mẫu thật.

`2408.11049` v4/v5 có file mẫu trong thư mục con với 8 include không tìm thấy đích;
root `main.tex` thật có 7 include tìm được đích. Cách đếm mọi include trước đây chọn
nhầm mẫu. Sau sửa, chọn `main.tex`, dùng 8/14 tệp TeX, nhận bibliography và giữ phần
cảm ơn/phụ lục. Các tín hiệu/trọng số khác và API/schema không đổi.

### 2.3. Nối file có giới hạn

Duyệt DFS từ root, thay tại chỗ các dạng `\input{file}`, `\input file`,
`\include{file}`, `\subfile{file}`, `\import{dir}{file}`. Thử đường dẫn từ
thư mục root trước, rồi từ thư mục hiện tại; thêm `.tex` khi thiếu đuôi.
`\include` chèn ranh giới đoạn bằng `\par`.

Đường dẫn được chuẩn hóa trong tập file đầu vào: cho phép `..` nếu kết quả vẫn
nằm trong gốc tương đối, từ chối đường dẫn tuyệt đối/drive/thoát gốc. Không có
bước mở file ngoài từ điển đầu vào.

Tập `active_stack` ngăn vòng lặp; root có độ sâu 0, giới hạn include là 15 cạnh.
File được include lặp vẫn được nối lặp đúng vị trí, nhưng số file dùng được đếm
theo đường dẫn duy nhất. Lệnh nạp không tìm thấy hoặc chưa hỗ trợ được ghi cảnh
báo kèm ID, version và file; cạnh lỗi không làm treo cả lượt parse.

```text
main.tex → sections/intro.tex → definitions.tex
         → sections/method.tex
unused.tex                         (không được dùng)
```

Khi đó có 5 file TeX tổng cộng, 4 file được dùng và 1 file bị bỏ.

### 2.4. Giữ nguồn tham khảo

Chọn `.bib` theo `\bibliography`/`\addbibresource` trên đường biên dịch.
Nếu không hề có khai báo, giữ toàn bộ `.bib` đầu vào làm fallback có cảnh báo.
Không tự lấy `.bib` không liên quan khi một khai báo cụ thể trỏ tới file thiếu.

Các khối `thebibliography` từ TeX, `.bbl` được include, `.bbl` trùng tên root
hoặc tương ứng tên bibliography được giữ lại, loại khối trùng byte trong đầu ra.
Nếu `.bbl` chỉ có `\bibitem` mà không bọc môi trường, giữ nguyên khối để giao D;
hierarchy cũng chuyển các `\bibitem` trần ra khỏi cây tới ranh giới đề mục tiếp theo.
Nếu bibliography thiếu dấu đóng, cảnh báo và dừng khối tại ranh giới đề mục/phụ lục
để không nuốt mất phần thân phía sau. D thực hiện chuyển `\bibitem` sang BibTeX.

**Khoảng thiếu tích hợp hiện tại:** `Corpus.tex_files()` chỉ trả `.tex/.bib`, chưa
trả `.bbl`. Do đó hỗ trợ `.bbl` của C mới có hiệu lực nếu caller truyền dữ liệu đó.
Cần A/D bổ sung đường truyền trong phần việc của họ và kiểm tra lại; không đổi
schema hoặc sửa `corpus.py` trong phần bàn giao C.

## 3. Xây dựng cây

### 3.1. Thứ tự xử lý và hợp đồng với B

Tách preamble/body bằng `begin{document}` và `end{document}`; nếu root fallback
không có cặp này, dùng toàn văn làm body. Gọi hook mở rộng macro của B với
preamble/body, rồi chuẩn hóa toán trước khi phân loại cấu trúc. Literal và URL
được che bằng token không trùng đầu vào, rồi phục hồi để hook không biến lệnh
minh họa thành cấu trúc thật.

Không import module thật của B trong module C. Caller truyền `CleanerHooks`
với bốn hàm đã khóa trong schema. Test dùng hook giả và kiểm tra dữ liệu truyền
qua giao diện; điều này không chứng minh thuật toán clean/split thật của B.

### 3.2. Stack cấp bậc

Mỗi bản có TeX tạo gốc `RawNode(0, None, "document", "Version N root")`.
Cấp mục:

```text
part < chapter < section < subsection < subsubsection < paragraph < subparagraph
```

Với một mục mới, pop stack tới mục gần nhất có cấp thấp hơn, gắn làm con rồi push.
Không tạo cấp giả khi tác giả nhảy từ section xuống paragraph. Đọc được dấu `*`,
tiêu đề ngắn tùy chọn và ngoặc lồng nhau. Abstract tạo heading riêng.

```text
document
├── Introduction                   heading
│   └── đoạn mở đầu                paragraph
│       └── một câu                sentence
└── Method                         heading
    ├── danh sách                  list
    │   └── ý thứ nhất             item
    │       └── đoạn/câu hoặc danh sách con
    └── phương trình               block_formula
```

### 3.3. Phân biệt cấu trúc và vùng bất khả phân

Danh sách `itemize`, `enumerate`, `description` tạo list/item, có thể lồng nhau.
Đoạn tách bằng dòng trống hoặc `\par`; câu do hook B trả về. Nội dung list/item
được tổng hợp từ con đã phát ra để tham khảo bị loại không lọt vào nội dung cha.

`figure`, `table`, `wrapfigure`, `algorithm` (và các dạng `*` được hỗ trợ) tạo lá
`figure`; phương trình tạo lá `block_formula`. Không nhận diện heading hoặc tách
câu bên trong các lá này. Inline math được chuyển nguyên vẹn cho hook B.
Giữ `\cite{...}` cho bước đổi khóa của D.

### 3.4. References và phục hồi cấu trúc hỏng

Chuẩn hóa tiêu đề qua hook clean, bỏ toàn bộ mục References/Bibliography/
Literature Cited/Works Cited cùng các mục con của nó cho tới mục cùng cấp hoặc
cao hơn, hoặc ranh giới phụ lục. Tách bibliography sang luồng D. Giữ mục cảm ơn,
phụ lục và nội dung phía sau; không dừng parse ngay tại References.

Môi trường thiếu dấu đóng được phục hồi tại đề mục tiếp theo hoặc cuối tài liệu,
kèm cảnh báo. Giới hạn lồng cấu trúc 64 mức: vượt giới hạn báo lỗi rõ ràng cho
pipeline thay vì âm thầm tạo cây phẳng sai. Lỗi trong hook B được propagate.

## 4. Kiểm chứng và giới hạn

Hai file test dùng nguồn tổng hợp kiểm tra comment/literal, root, DFS, đường dẫn,
vòng lặp, giới hạn sâu, BibTeX/BBL, heading, list, math/figure, References, phiên bản
rỗng, tính bất biến đầu vào và ID/parent. Có kiểm tra tích hợp C thật qua `Stages`
của A với B/D giả trên corpus tổng hợp; không tải mạng hoặc chạy TeX.

Kiểm tra local ngày 08/11/2026, base `ac2c174`: **98 test C**, tổng suite
**359 passed (27,06 s)**; Ruff check/format đạt. Sáu trường hợp mới bảo vệ lựa chọn
root khi template trỏ sai và đường cha an toàn/đuôi `.TeX`. Trên Windows sandbox, đặt
`TEMP`/`TMP` của tiến trình test dưới `.venv/m2c-temp` và basetemp pytest mới
để tránh ký tự `#` trong Temp mặc định làm hỏng URI SQLite của test checkpoint.
Đây là thời gian chạy unit test, không phải benchmark parser.

**Đã thử C độc lập trên 10 bài thật / 26 phiên bản**, thêm 3 phiên bản đối chứng
(gồm `no_source` và `not_found`). Người dùng xác nhận corpus do leader cung cấp,
đã giải nén ở `arxiv-data/`. Dùng `Corpus` đọc nguồn, không sửa corpus hoặc tải mạng.
Hook truyền nguyên văn nên kết quả chưa chứng minh thuật toán B hoặc pipeline B/D/E.

Mẫu có 349 tệp khớp kích thước/SHA-256 metadata. C dùng **265/324 TeX**, giữ cây và
các marker References theo kiểm tra, không phát sinh ngoại lệ; **6/26 phiên bản**
cảnh báo bibliography thiếu trong subset. Chưa biết tệp thiếu từ gói gốc hay lúc
chuẩn bị subset. Hai bản v2/v3 của `2408.11047` mỗi bản có một khối 198 bibitem được
tách khỏi cây. Chưa thực hiện trích trường hoặc dedup tham khảo của D.

[Bảng từng phiên bản](02_c_real_sample_check.md) và [JSON](c_real_sample_check.json)
ghi root, số file, số nút từng loại, vị trí đề mục đối chiếu và cảnh báo:

| Bằng chứng | Cần ghi |
| --- | --- |
| Phạm vi | arXiv ID, version, trạng thái nguồn |
| Gom nguồn | Root được chọn, tổng/dùng/bỏ, cảnh báo file thiếu/chu trình |
| Cây | Số nút theo loại, cha hợp lệ, không chu trình, không rò References |
| Đối chiếu | Mục/đoạn chọn mẫu khớp TeX, giữ Abstract/cảm ơn/phụ lục |
| Giới hạn | Hook truyền nguyên văn, chưa có B/D thật; thiếu bibliography và chưa có `.bbl` trong subset |

Không dùng kết quả test tổng hợp làm số liệu parse 25.000 ID. Chỉ đánh dấu M2-C
hoàn tất sau kiểm chứng với B/D thật và xử lý khoảng thiếu `.bbl`. Bản thảo chưa được build
trong repo sách; E sẽ ghép và kiểm tra `jupyter-book build -W`.

Parser không thực thi điều kiện TeX, không mô phỏng `\includeonly`, macro tên file
động, catcode hoặc toàn bộ package LaTeX. Root election là heuristic; fallback
latin-1 có thể đọc được byte nhưng chưa chắc đúng bảng mã tác giả. Cảnh báo cho
include thiếu có nghĩa kết quả có thể chỉ là một phần; phải phân tích khi báo cáo
độ bao phủ. Literal thiếu dấu đóng được bảo toàn tới cuối văn bản. Không tuyên bố
chính xác tuyệt đối hoặc hỗ trợ mọi tài liệu arXiv.
