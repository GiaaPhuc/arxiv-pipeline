# Phần D — Trích xuất tham khảo và khử trùng lặp hai tầng

Bản thảo bàn giao cho nửa sau chương 2 của `arxiv-book`. Checkout hiện tại
chưa chứa repo sách. Nội dung này mô tả triển khai độc lập và kiểm thử tổng hợp;
chưa phải kết quả chạy corpus thật hoặc benchmark Colab. Sau merge đã có test
tích hợp dùng module C/B/D thực và validator E trên corpus tổng hợp.

## Hợp đồng và cách tích hợp

`src/parsing/models.py` dùng hợp đồng chung của A từ `origin/main`, gồm
`CleanerHooks`, `PaperStats` và `PaperResult`. Bổ sung tương thích ngược
`PaperResult.bib_fields_before: dict[str, int] | None = None` để chuyển số đếm
trước dedup qua worker; caller cũ vẫn dùng được. Không thay đổi `crawler/models.py` của M1.

Mỗi lần gọi dedup chỉ xử lý một bài báo và toàn bộ phiên bản của bài đó.
`deduplicate_references(arxiv_id, entries)` nhận thêm ID bài báo một cách tường
minh vì `RawBibEntry` không chứa ID nhưng `BibtexRow` cần trường này. Adapter
trong `default_stages()` truyền ID rỗng; pipeline điền ID thật ngay sau dedup
theo giao diện một tham số đã có của A.

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
có thể bổ sung URL arXiv, kể cả tiền tố `arXiv:`. Comment `%` giữa các trường
được loại trước khi cân bằng ngoặc; dấu phần trăm trong giá trị URL vẫn giữ nguyên. Lỗi cú pháp được báo bằng `ValueError`; pipeline cần
bắt lỗi và ghi nhận bài/phiên bản tương ứng.

Với `bibitem`, lấy DOI, URL, arXiv ID, năm và `bibinfo` trước. Sau đó thử tiêu đề
trong ngoặc kép, lệnh nhấn mạnh, cấu trúc tác giả–tiêu đề–tạp chí theo dấu chấm,
hoặc mẫu preprint arXiv. Trường không suy ra được để rỗng. Khi có cả DOI và arXiv,
DOI lưu riêng và URL ưu tiên arXiv để giữ tín hiệu đối sánh M3. Không nhận dấu
umlaut LaTeX là ngoặc kép; hỗ trợ cả nhóm `{\em ...}`/`{\it ...}`. Khi phần
nhấn mạnh là venue, lấy tác giả/tiêu đề từ tiền tố. Bảo vệ viết tắt học thuật
khi tách câu và che DOI, URL, arXiv ID, dải trang trước khi tìm năm.

Giới hạn: bộ đọc `.bib` hiện dùng cú pháp `@type{...}`, chưa hỗ trợ dạng
`@type(...)`, kế thừa `crossref`, macro khai báo sau nơi dùng hoặc giải mã đầy đủ
các dấu tiếng nước ngoài của TeX. Các mẫu `bibitem` là heuristic, không cam kết
trích xuất chính xác mọi kiểu bibliography; cần đánh giá trên corpus thật.

## Khử trùng tham khảo

Union-Find gom bản ghi theo DOI/arXiv ID; hoặc tiêu đề có độ tương đồng
`SequenceMatcher >= 0.85` kèm cùng năm/họ tác giả đầu và tiêu đề chuẩn hóa
dài ít nhất 12 ký tự. Khóa giống nhau giữa
phiên bản chỉ là tín hiệu yếu: hiện yêu cầu tiêu đề chuẩn hóa giống nhau và
không xung đột năm. Không gộp bản ghi trống chỉ vì trùng khóa số.

Kiểm tra xung đột DOI/arXiv ở cấp cụm để tránh một bản ghi thiếu trường nối hai
cụm trái ngược. Khóa chuẩn ưu tiên bản ghi của phiên bản sớm, rồi thứ tự khóa;
khóa bị tái sử dụng cho tài liệu khác được thêm hậu tố không đụng khóa gốc.
Với cùng `(version, key)`, ưu tiên `.bib` hơn `bibitem`, rồi giữ khai báo đầu
tiên theo thứ tự đầu vào và ghi cảnh báo. Chỉ bổ sung trường thiếu nếu DOI/arXiv
không xung đột. Đây là quy tắc xử lý khóa mơ hồ, không phải bằng chứng hai mục
chắc chắn cùng tài liệu.

Hợp nhất lấy giá trị chuỗi/danh sách đầy đủ nhất theo độ dài biểu diễn, giữ một
danh sách tác giả hoàn chỉnh thay vì nối tác giả từ nhiều bản. Năm lấy giá trị
phổ biến nhất (hòa thì ưu tiên bản ghi sớm). Loại mục ưu tiên loại khác `misc`.
Quy tắc tất định nhưng vẫn cần kiểm tra thủ công các trường xung đột trong pilot.
Chỉ mục ứng viên theo DOI/arXiv, năm, họ tác giả đầu và khóa giới hạn các cặp
cần so sánh; kiểm tra bằng chứng và độ dài trước `SequenceMatcher`. Trường hợp
xấu nhất nhiều mục chung block vẫn O(R²); chưa có benchmark corpus thật.

## Đổi trích dẫn và gộp nội dung

Đổi các khóa trong `cite` theo `(version, old_key)`, loại khóa lặp trong cùng
lệnh, giữ khóa chưa có ánh xạ. Nhận cùng tập biến thể với validator E, gồm
`citealp`, `citealt`, `citeauthor`, `citeyear`, `autocite` và khoảng trắng trước
đối số tùy chọn. Sau đó dùng kho `(kind, content)` để tái sử dụng
ID; mỗi document root giữ ID riêng. Không sửa các đối tượng đầu vào.

`hierarchy` chỉ chứa cạnh con–cha, không có cạnh tự trỏ cho root. Cạnh giống
nhau trong một phiên bản chỉ lưu một lần. Khi các phần tử dùng chung có nhiều
cha, kết quả là DAG, không nhất thiết còn là cây theo occurrence. Schema hiện
tại không lưu thứ tự hay số lần xuất hiện của một phần tử trùng trong cùng cha.

Phát hiện cha thiếu, ID cục bộ lặp và chu trình của cây đầu vào. Khi gộp nội
dung tạo self-loop/back-edge, bỏ cạnh thừa theo duyệt cha trước con; các con
vẫn nối vào phần tử đã gộp. Giữ duy nhất `(kind, content)`, DAG và khả năng
đi từ root tới toàn bộ nội dung; không giữ occurrence của heading trùng.

## Xuất dữ liệu và thống kê

Ghi ba thư mục `elements/`, `hierarchy/`, `bibtex/`, mặc định 500 bài/shard.
`close()` ghi lô cuối; context manager gọi tự động khi hoàn tất bình thường.
Corpus rỗng tạo bảng có schema và 0 row groups để tương thích Hugging Face.
Với datasets 5.2, bước prepare chấp nhận shard rỗng; tải riêng split hoàn toàn
rỗng vẫn có thể báo `no data` theo hành vi thư viện.
Nén ZSTD; cột bắt buộc non-null; `kind`/`entrytype` dùng dictionary<int8, string>,
`version`/`year` dùng int32, `authors` là list<string>. Root không sinh cạnh.

Pipeline gọi `add_paper(PaperResult)`; exporter cũng giữ API các danh sách dòng
cho test độc lập. CSV bổ sung các trường `PaperStats` (`n_versions`,
`n_versions_empty`, `n_elements`, ...), thời gian từng bước và lỗi để validator E
kiểm tra trực tiếp. Pipeline tính `bib_fields_before` trước dedup để xuất đủ
các cột độ phủ ngay cả khi chạy nhiều worker. Bài lỗi chỉ vào log và được thử
lại khi resume; không ghi dòng CSV/Parquet giả cho bài lỗi.

Đầu ra thống kê:

- `m2_summary.json`: tổng bài, phiên bản, phiên bản rỗng, số dòng và số shard.
- `m2_papers_stats.csv`: số lượng trước/sau dedup của từng bài; số tham khảo có
  `title/authors/year/doi/journal/url` trước/sau để tính độ phủ trường.
- `m2_elements_kind.csv`: phân bố loại phần tử sau dedup.

Số trước dedup để trống nếu caller không cung cấp, không suy đoán từ kết quả cuối.
Số liệu thời gian, RAM, file TeX dùng/bỏ và độ sâu cây cần được bổ sung từ
pipeline/validator của A/C/E. Một lô dữ liệu giữ trong RAM; chưa đo giới hạn
450 MB. Mặc định từ chối đầu ra đã tồn tại. Dùng `run_parse.py --resume` với
cùng `--out-dir`/`--stats-dir` để nối shard kế tiếp. Exporter khôi phục ID bài
từ CSV đã commit, kiểm tra số dòng với shard và tổng hợp thống kê cũ + mới.
`_export_pending.json` lưu chỉ số shard và vị trí CSV trước mỗi lô: nếu tiến
trình ngắt trong khi ghi, lần resume xóa shard của lô dở và cắt CSV về vị trí
cũ. Nếu ngắt sau commit nhưng trước `_done.txt`, ID từ CSV sửa lại checkpoint.
Lỗi đã xử lý thành công được gỡ khỏi `_errors.jsonl`; lịch sử ở `parse.log`.
Cơ chế này dành cho một tiến trình ghi, phục hồi gián đoạn tiến trình; chưa
kiểm chứng mất điện hoặc hỏng filesystem. Output cũ không nhất quán giữa CSV
và shard bị từ chối, cần chạy lại vào thư mục mới.

## Kiểm thử độc lập

```bash
pytest src/tests/test_bib_extractor.py src/tests/test_dedup.py src/tests/test_export_m2.py
```

Test dùng chuỗi và thư mục tạm, không gọi mạng. Bao phủ kịch bản ba phiên bản
`vaswani2017 → vit_transformer`, bổ sung DOI nhưng giữ năm cũ, gộp câu sau khi
đổi khóa; bẫy khóa số, xung đột DOI, chu trình, schema Parquet, lô cuối và bản rỗng.
Các test này chưa thay thế pilot trên nguồn LaTeX thật và validator M2 của E.
