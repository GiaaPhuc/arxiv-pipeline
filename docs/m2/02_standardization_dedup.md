# Phần D — Trích xuất tham khảo và khử trùng lặp hai tầng

Bản thảo bàn giao cho nửa sau chương 2 của `arxiv-book`. Checkout hiện tại
chưa chứa repo sách. Nội dung này mô tả triển khai độc lập và kiểm thử tổng hợp;
chưa phải kết quả chạy corpus thật hoặc benchmark Colab.

## Hợp đồng và cách tích hợp

`src/parsing/models.py` là **đề xuất hợp đồng M2**, cần A đối chiếu trước khi ghép
các module. Không thay đổi hợp đồng `crawler/models.py` của M1.

Mỗi lần gọi dedup chỉ xử lý một bài báo và toàn bộ phiên bản của bài đó.
`deduplicate_references(arxiv_id, entries)` nhận thêm ID bài báo một cách tường
minh vì `RawBibEntry` không chứa ID nhưng `BibtexRow` cần trường này.

```python
from pathlib import Path
from parsing.bib_extractor import parse_bib_file, parse_bibitems
from parsing.dedup import (
    deduplicate_references,
    rewrite_citations_and_dedup_elements,
)
from parsing.export_m2 import ShardedParquetExporter

# units: CompilationUnit từ C; trees: dict[int, list[RawNode]] từ C/B.
# Phiên bản không có nguồn phải vẫn có mặt trong trees với giá trị [].
entries = []
for unit in units:
    for source in unit.bib_files.values():
        entries.extend(parse_bib_file(source, unit.version))
    entries.extend(parse_bibitems(unit.raw_bibitems_blocks, unit.version))
bibtex, rename = deduplicate_references(arxiv_id, entries)
elements, hierarchy = rewrite_citations_and_dedup_elements(arxiv_id, trees, rename)

# Khởi tạo một exporter bên ngoài vòng lặp các bài của pipeline.
with ShardedParquetExporter(Path("data/m2"), stats_dir=Path("data/stats")) as output:
    output.add_paper(
        arxiv_id,
        elements,
        hierarchy,
        bibtex,
        versions=sorted(trees),
        empty_versions=[v for v, nodes in trees.items() if not nodes],
        elements_before=sum(len(nodes) for nodes in trees.values()),
        raw_bib_entries=entries,
    )
```

## Trích xuất tham khảo

Bộ đọc `.bib` duyệt ngoặc nhọn cân bằng, hỗ trợ giá trị có ngoặc lồng nhau,
chuỗi trong dấu nháy, `@string` đã định nghĩa trước khi sử dụng và phép nối `#`.
Tách tác giả theo `and` ngoài ngoặc để giữ tên tổ chức. `eprint`/`arxivid` hợp lệ
có thể bổ sung URL arXiv. Lỗi cú pháp được báo bằng `ValueError`; pipeline cần
bắt lỗi và ghi nhận bài/phiên bản tương ứng.

Với `bibitem`, lấy DOI, URL, arXiv ID, năm và `bibinfo` trước. Sau đó thử tiêu đề
trong ngoặc kép, lệnh nhấn mạnh, cấu trúc tác giả–tiêu đề–tạp chí theo dấu chấm,
hoặc mẫu preprint arXiv. Trường không suy ra được để rỗng. Khi có cả DOI và arXiv,
DOI lưu riêng và URL ưu tiên arXiv để giữ tín hiệu đối sánh M3.

Giới hạn: bộ đọc `.bib` hiện dùng cú pháp `@type{...}`, chưa hỗ trợ dạng
`@type(...)`, kế thừa `crossref`, macro khai báo sau nơi dùng hoặc giải mã đầy đủ
các dấu tiếng nước ngoài của TeX. Các mẫu `bibitem` là heuristic, không cam kết
trích xuất chính xác mọi kiểu bibliography; cần đánh giá trên corpus thật.

## Khử trùng tham khảo

Union-Find gom bản ghi theo DOI/arXiv ID; hoặc tiêu đề có độ tương đồng
`SequenceMatcher >= 0.85` kèm cùng năm/họ tác giả đầu. Khóa giống nhau giữa
phiên bản chỉ là tín hiệu yếu: hiện yêu cầu tiêu đề chuẩn hóa giống nhau và
không xung đột năm. Không gộp bản ghi trống chỉ vì trùng khóa số.

Kiểm tra xung đột DOI/arXiv ở cấp cụm để tránh một bản ghi thiếu trường nối hai
cụm trái ngược. Khóa chuẩn ưu tiên bản ghi của phiên bản sớm, rồi thứ tự khóa;
khóa bị tái sử dụng cho tài liệu khác được thêm hậu tố không đụng khóa gốc.
Trùng khóa nhưng khác tài liệu ngay trong một phiên bản báo lỗi vì không thể
xác định `cite` đang trỏ tới tài liệu nào.

Hợp nhất lấy giá trị chuỗi/danh sách đầy đủ nhất theo độ dài biểu diễn, giữ một
danh sách tác giả hoàn chỉnh thay vì nối tác giả từ nhiều bản. Năm lấy giá trị
phổ biến nhất (hòa thì ưu tiên bản ghi sớm). Loại mục ưu tiên loại khác `misc`.
Quy tắc tất định nhưng vẫn cần kiểm tra thủ công các trường xung đột trong pilot.
Hiện so sánh từng cặp tham khảo của một bài: O(R²), chưa có chỉ mục ứng viên cho
bibliography rất lớn.

## Đổi trích dẫn và gộp nội dung

Đổi các khóa trong `cite` theo `(version, old_key)`, loại khóa lặp trong cùng
lệnh, giữ khóa chưa có ánh xạ. B cần chuẩn hóa các biến thể `citep/citet` về
`cite` trước khi gọi module D. Sau đó dùng kho `(kind, content)` để tái sử dụng
ID; mỗi document root giữ ID riêng. Không sửa các đối tượng đầu vào.

`hierarchy` chỉ chứa cạnh con–cha, không có cạnh tự trỏ cho root. Cạnh giống
nhau trong một phiên bản chỉ lưu một lần. Khi các phần tử dùng chung có nhiều
cha, kết quả là DAG, không nhất thiết còn là cây theo occurrence. Schema hiện
tại không lưu thứ tự hay số lần xuất hiện của một phần tử trùng trong cùng cha.

Phát hiện cha thiếu, ID cục bộ lặp và chu trình. Nếu việc gộp nội dung tạo chu
trình (ví dụ hai heading tổ tiên–con cháu cùng nội dung), báo lỗi thay vì xuất
đồ thị sai. Cần A/C/E thống nhất biểu diễn trường hợp này trước khi chạy toàn bộ.

## Xuất dữ liệu và thống kê

Ghi ba thư mục `elements/`, `hierarchy/`, `bibtex/`, mặc định 500 bài/shard.
`close()` ghi lô cuối; context manager gọi tự động khi hoàn tất bình thường.
Corpus rỗng vẫn tạo các bảng rỗng có schema. `kind`/`entrytype` dùng dictionary,
`version`/`year` dùng int32, `authors` là list<string>. Root không sinh cạnh.

Đầu ra thống kê:

- `m2_summary.json`: tổng bài, phiên bản, phiên bản rỗng, số dòng và số shard.
- `m2_papers_stats.csv`: số lượng trước/sau dedup của từng bài; số tham khảo có
  `title/authors/year/doi/journal/url` trước/sau để tính độ phủ trường.
- `m2_elements_kind.csv`: phân bố loại phần tử sau dedup.

Số trước dedup để trống nếu caller không cung cấp, không suy đoán từ kết quả cuối.
Số liệu thời gian, RAM, file TeX dùng/bỏ và độ sâu cây cần được bổ sung từ
pipeline/validator của A/C/E. Một lô dữ liệu giữ trong RAM; chưa đo giới hạn
450 MB. Exporter yêu cầu thư mục đầu ra mới, chưa hỗ trợ resume. Từng file ghi
qua file tạm rồi rename; cả ba bảng không phải một giao dịch nguyên tử. Nếu có
lỗi giữa chừng cần dùng thư mục đầu ra mới cho lần chạy lại.

## Kiểm thử độc lập

```bash
pytest src/tests/test_bib_extractor.py src/tests/test_dedup.py src/tests/test_export_m2.py
```

Test dùng chuỗi và thư mục tạm, không gọi mạng. Bao phủ kịch bản ba phiên bản
`vaswani2017 → vit_transformer`, bổ sung DOI nhưng giữ năm cũ, gộp câu sau khi
đổi khóa; bẫy khóa số, xung đột DOI, chu trình, schema Parquet, lô cuối và bản rỗng.
Các test này chưa thay thế pilot trên nguồn LaTeX thật và validator M2 của E.
