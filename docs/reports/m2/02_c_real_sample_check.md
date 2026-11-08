# Kiểm chứng phần C trên 10 bài thật — 08/11/2026

> Nhánh `m2/c-gathering-hierarchy`, nền `ac2c174`; thay đổi C còn local, chưa commit/push.
> Kết quả sau sửa chọn root: **26/26 phiên bản mẫu và 3/3 phiên bản đối chứng đạt các kiểm tra bên dưới**.
> **M2-C vẫn 🟡**: dùng hook truyền nguyên văn, chưa tích hợp B/D thật hoặc validator E.
> Bằng chứng máy đọc: [c_real_sample_check.json](c_real_sample_check.json), có thời điểm chạy,
> hash code, hash metadata, số nút từng loại, đề mục đối chiếu và toàn bộ cảnh báo từng phiên bản.

## 1. Nguồn dữ liệu và phạm vi

Người dùng xác nhận `D:/Project_NMKHDL/arxiv-data` do leader cung cấp và đã giải nén.
Không tải mạng, không thực thi TeX, không sửa corpus hay manifest. Chưa đối chiếu gói nén gốc.

Kiểm kê cục bộ: **1.000 bài / 1.678 phiên bản**; metadata ghi **1.526 `ok`,
148 `no_source`, 4 `not_found`**. Số thư mục phiên bản khớp `num_versions` từng bài.
Không thấy tệp `.bbl` trong subset. Đây không phải số liệu hoàn tất corpus 25.000 ID.
Chưa validate toàn bộ corpus hoặc đối chiếu SHA-256 của tất cả 1.000 bài.

Mẫu chọn có chủ đích: bài một tệp, nhiều phiên bản, nhiều root ứng viên, include lồng
thư mục, macro/toán, bibliography nội dòng và bài có 101 tệp TeX. Đối chiếu **349 tệp
(324 TeX + 25 BibTeX, tính theo phiên bản)** với kích thước/SHA-256 metadata: không sai khác.
Không dùng mẫu này để suy tỷ lệ thành công toàn corpus.

## 2. Cách kiểm tra

Đọc bằng `Corpus(Path("arxiv-data"))`, gọi hai hàm C trực tiếp, không gọi
`default_stages()` hoặc pipeline/export của A/D. Hook mở rộng macro, chuẩn hóa toán
và clean trả nguyên văn; hook split trả một câu cho mỗi đoạn không rỗng.
Vì vậy số `sentence` bằng số `paragraph`, không đại diện chất lượng tách câu của B;
các môi trường như `align` chưa được B chuẩn hóa nên số formula cũng chưa phải số cuối.

Các kiểm tra thực hiện trên mọi phiên bản:

- Root đúng tệp đã đối chiếu; `total_tex_files = used_tex_files + unused_tex_files`.
- Nút có ID liên tục; đúng một gốc nếu có TeX; mỗi cha có ID nhỏ hơn con.
  Điều này bảo đảm cây liên thông, không chu trình; sentence/formula/figure là lá.
- Không có `\bibitem` hoặc `\begin{thebibliography}` trong nội dung nút; không có heading
  tên References/Bibliography/Literature Cited/Works Cited khớp trực tiếp sau đổi thường.
  Đây là kiểm tra marker, chưa chứng minh không còn tham khảo viết bằng mọi biến thể macro.
- Ít nhất hai đề mục chọn mẫu mỗi bài/phiên bản xuất hiện cả trong nguồn TeX và cây;
  JSON lưu đường dẫn/dòng của từng đề mục. Đối chiếu thêm cảm ơn/phụ lục ở v4/v5 của
  `2408.11049` và bibliography nội dòng của `2408.11047` v2/v3.
- Mapping bytes đầu vào không bị thay đổi. Phiên bản rỗng trả `root=None`, cây rỗng.

## 3. Kết quả từng phiên bản

`T/D/B` = số tệp TeX tổng/dùng/bỏ; `nút` là tổng mọi loại, chi tiết trong JSON.
`bib/khối` là số tệp BibTeX và khối tham khảo, không phải số tham khảo đã trích xuất/dedup.
Tất cả 26 phiên bản mẫu có metadata `ok` và không phát sinh ngoại lệ từ C.

| arXiv ID | v | Root | TeX T/D/B | Nút | bib/khối | Cảnh báo |
| --- | --- | --- | --- | --- | --- | --- |
| 2408.11045 | 1 | `main.tex` | 1/1/0 | 936 | 0/0 | thiếu `references.bib` |
| 2408.11045 | 2 | `main.tex` | 1/1/0 | 941 | 0/0 | thiếu `references.bib` |
| 2408.11045 | 3 | `main.tex` | 1/1/0 | 967 | 0/0 | thiếu `references.bib` |
| 2408.11047 | 1 | `main.tex` | 1/1/0 | 648 | 1/0 | — |
| 2408.11047 | 2 | `main.tex` | 1/1/0 | 651 | 0/1 | — |
| 2408.11047 | 3 | `main.tex` | 1/1/0 | 651 | 0/1 | — |
| 2408.11048 | 1 | `main.tex` | 2/2/0 | 181 | 1/0 | — |
| 2408.11048 | 2 | `main.tex` | 2/2/0 | 188 | 1/0 | — |
| 2408.11049 | 1 | `main.tex` | 16/7/9 | 169 | 1/0 | — |
| 2408.11049 | 2 | `main.tex` | 16/8/8 | 176 | 1/0 | — |
| 2408.11049 | 3 | `main.tex` | 16/8/8 | 174 | 1/0 | — |
| 2408.11049 | 4 | `main.tex` | 14/8/6 | 218 | 1/0 | — |
| 2408.11049 | 5 | `main.tex` | 14/8/6 | 218 | 1/0 | — |
| 2408.11051 | 1 | `main.tex` | 6/6/0 | 164 | 1/0 | — |
| 2408.11051 | 2 | `aaai25.tex` | 1/1/0 | 171 | 1/0 | — |
| 2408.11052 | 1 | `neurips_2020.tex` | 2/2/0 | 232 | 1/0 | — |
| 2408.11052 | 2 | `main.tex` | 2/2/0 | 282 | 1/0 | — |
| 2408.11052 | 3 | `main.tex` | 2/2/0 | 297 | 1/0 | — |
| 2408.11052 | 4 | `main.tex` | 2/2/0 | 297 | 1/0 | — |
| 2408.11053 | 1 | `conference_101719.tex` | 6/6/0 | 144 | 0/0 | thiếu `ref.bib` |
| 2408.11053 | 2 | `conference_101719.tex` | 8/8/0 | 240 | 1/0 | — |
| 2408.11081 | 1 | `main.tex` | 50/49/1 | 478 | 3/0 | — |
| 2408.11081 | 2 | `main.tex` | 52/51/1 | 521 | 3/0 | — |
| 2408.11111 | 1 | `passive_RB.tex` | 3/3/0 | 982 | 0/0 | thiếu 4 bibliography |
| 2408.11111 | 2 | `passive_RB.tex` | 3/3/0 | 992 | 0/0 | thiếu 4 bibliography |
| 2408.11954 | 1 | `master.tex` | 101/81/20 | 2047 | 1/0 | — |

Tổng mẫu: **324 TeX, dùng 265, bỏ 59** theo đường include tĩnh đã chọn.
**6/26 phiên bản** có cảnh báo bibliography thiếu trong subset cục bộ; không còn
cảnh báo include trên mẫu sau sửa root. Không coi các tham khảo bị thiếu là đã thu đủ.

| Đối chứng | Trạng thái | Kết quả |
| --- | --- | --- |
| 2408.11059 v1 | `no_source` | root=None, cây rỗng |
| 2408.11176 v1 | `ok` | `UnirationalFib.tex`, cây hợp lệ; thiếu `UnirationalFib.bib` |
| 2408.11176 v2 | `not_found` | root=None, cây rỗng |

## 4. Lỗi chọn root đã sửa và giới hạn đầu vào

`2408.11049` v4/v5 có `main.tex` thật và file mẫu
`ICLR_2025_Template/main_ranajoy.tex`. Thuật toán cũ thưởng mọi include:
file mẫu có 8 lệnh nhưng không tìm thấy đích nào theo thư mục của nó; root thật có
7 lệnh tìm được đích. Cũ chọn template, chỉ dùng 1/14 TeX và tạo 2 heading.

Sau sửa, chỉ cộng `25` điểm cho include có đích phân giải được trong mapping từ thư
mục của root ứng viên. Giữ nguyên trọng số/tín hiệu khác và API/schema. Dùng chung
cách đọc argument include cho chấm điểm và DFS. Hiện chọn `main.tex`, dùng **8/14 TeX**,
nhận `iclr2025_conference.bib`, có **25 heading**, giữ `Acknowledgements` và `Appendix`.
Heuristic vẫn không phải trình biên dịch TeX hoặc bằng chứng mọi root đều đúng.

Đã thêm 6 trường hợp test tổng hợp: 5 biến thể include tái hiện template trỏ sai
(vốn thất bại trước sửa) và trường hợp đường cha an toàn/đuôi `.TeX`.
Kết quả: **98 test C**, toàn suite **359 passed (27,06 s)**; Ruff check/format đạt.
Thời gian này là unit test, không phải benchmark parser. TEMP/TMP ở `.venv/m2c-temp`;
pytest dùng basetemp UUID mới, không đụng thư mục test hay worktree cũ.

Bibliography chưa có trong subset:

- `2408.11045` v1–v3: khai báo `references`, chỉ có `main.tex`.
- `2408.11053` v1: khai báo `ref`; ví dụ `thebibliography` trong file đang bị comment.
- `2408.11111` v1/v2: khai báo `CVs`, `Math`, `RB`, `mk` (v1 dùng tiền tố `../`).
- Đối chứng `2408.11176` v1: khai báo `UnirationalFib` nhưng không có tệp đó.

Chưa xác định các tệp thiếu từ gói nguồn gốc hay từ lúc chuẩn bị subset.
Không tự tải bù, không suy bibliography từ references S2, không tạo BibTeX giả.

## 5. Bàn giao phụ thuộc B/D/A/E

Đây là danh sách để Quỳnh chuyển tiếp khi cần; **chưa gửi tin nhóm**.
Tình trạng module chỉ được kiểm tra ở checkout `ac2c174` + thay đổi C local;
chưa fetch/đối chiếu code mới trên remote.

| Bên phối hợp | Việc cần làm / tiêu chí xác nhận |
| --- | --- |
| B | Cần 4 hàm theo `CleanerHooks`: `extract_and_expand_macros(preamble, body)`, `normalize_math(text)`, `clean_latex_content(text, is_figure=False)`, `split_sentences(text)`. A nối qua `default_stages()`, chạy lại đủ 26 phiên bản; kiểm tra macro, toán, câu và giữ `\cite{...}`. Chưa thay hook thật bằng kết quả identity. |
| D | Kiểm tra `CompilationUnit.bib_files` và hợp các khối từ gathering/hierarchy theo caller A. `2408.11047` v2/v3 mỗi bản có 1 khối, **198 bibitem**; cần trích trường/khử trùng thật. Phân biệt tham khảo thiếu với thực sự không có; xử lý phiên bản rỗng. Chưa kiểm tra 3 bảng Parquet. |
| A + D | Xác nhận bibliography thiếu trong các ID trên có thể bổ sung từ dữ liệu gốc không. Nối `.bbl` qua `Corpus.tex_files()` trong phần việc owner; hiện hàm chỉ trả `.tex/.bib`, subset không có `.bbl`, nên cần fixture tích hợp riêng. C đã hỗ trợ khi nhận bytes. Không đổi schema như tác dụng phụ. |
| A | Ghép module B/D thật, chạy pilot; dùng `--data-dir arxiv-data` theo CLI hiện có. Không coi lượt gọi trực tiếp hai hàm C là pipeline end-to-end, số parse 25k hay benchmark Colab. |
| E | Nhận `01_parsing_hierarchy.md` và bằng chứng này; chạy validator trên đầu ra thật rồi ghép Book/build `-W`. Đối chiếu lại bibliography, cảm ơn/phụ lục với C sau khi có B/D thật. Chưa sửa/build repo sách trong lượt này. |

M2-C giữ **🟡** trong STATUS: kiểm chứng cấu trúc C độc lập đã có bằng chứng, còn
kiểm chứng với B/D thật và đường `.bbl`. Commit/push/PR là bước riêng theo yêu cầu người dùng.
