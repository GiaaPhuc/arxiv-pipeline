# Project Milestone 1 — Thu thập dữ liệu (Data Crawling)

> Bản dịch tiếng Việt từ: [https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/crawling](https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/crawling)
> Dự án arXiv — Môn Nhập môn Khoa học Dữ liệu (Introduction to Data Science)

## Mục tiêu

Trong milestone này, sinh viên sẽ vận dụng kiến thức về thu thập dữ liệu (scraping hay crawling) đã học trong các bài giảng lý thuyết để thu thập dữ liệu từ một kho lưu trữ trực tuyến. Bài thực hành này nhằm giúp bạn chuyển hóa kiến thức thành kỹ năng hữu ích về khía cạnh kỹ thuật (engineering) trong hành trình khoa học dữ liệu của mình. Tuy nhiên, đừng nghĩ rằng milestone này, cũng như các milestone khác, sẽ bao quát mọi kỹ năng cần thiết trước khi tham gia các dự án thực tế — bạn phải tiếp tục học hỏi ngay cả sau khi kết thúc môn học này!

Mục tiêu năm nay là arXiv, kho lưu trữ truy cập mở với hơn hai triệu bài báo. Việc thu thập dữ liệu arXiv vốn đã là một thực hành quen thuộc trong các môn kỹ thuật dữ liệu, tuy nhiên hầu hết chỉ tập trung riêng vào siêu dữ liệu (metadata) thay vì tài nguyên toàn văn. Ở đây, bạn sẽ đi trước một bước bằng cách thu thập **mã nguồn LaTeX toàn văn** của các bài báo thay vì các file PDF khó xử lý. Kho ngữ liệu (corpus) được xây dựng trong milestone này là nền tảng cho phần phân tích cú pháp (parsing) và học máy ở Milestone 2 và 3, vì vậy hãy thu thập đầy đủ và tổ chức đúng cách.

## Mô tả

Milestone này gồm ba mảng chính: xác định các bài báo được giao, tải xuống các file mã nguồn toàn văn của chúng, và trích xuất thông tin tham khảo (reference) có cấu trúc.

### Khám phá mục bài báo (Entry Discovery)

Bước đầu tiên trong mọi quy trình crawl arXiv là xác định những bài báo cần thu thập. Trong dự án này, phạm vi được giao sẽ đến dưới dạng các arXiv ID thuần túy, vì vậy các phương pháp dưới đây là một "hộp công cụ" chứ không phải một nhiệm vụ bắt buộc: API sẽ chuyển các ID được giao thành siêu dữ liệu bài báo và cho biết danh sách các phiên bản của từng bài, còn OAI-PMH và bộ dữ liệu Kaggle cho thấy cách thực hiện việc khám phá tương tự ở quy mô toàn kho lưu trữ. Việc triển khai một đợt thu thập toàn bộ kho ở quy mô lớn _không_ thuộc phạm vi của milestone này.

#### arXiv API cho việc khám phá có mục tiêu

arXiv API ([http://export.arxiv.org/api/query](http://export.arxiv.org/api/query)) cung cấp giao diện HTTP để truy xuất thông tin bài báo dựa trên từ khóa tìm kiếm, tác giả hoặc danh mục. API trả về kết quả ở định dạng Atom XML, có thể được phân tích bằng chương trình. Phương pháp này đặc biệt tiện lợi cho các truy vấn có mục tiêu hoặc cập nhật hằng ngày các bài nộp mới.

**Các tham số chính và cách dùng** — arXiv API chấp nhận một số tham số hữu ích:

- `search_query`: Tìm kiếm theo trường (ví dụ `ti:neural AND cat:cs.AI`)
- `id_list`: Danh sách arXiv ID phân tách bằng dấu phẩy để tra cứu trực tiếp
- `start`, `max_results`: Điều khiển phân trang
- `sortBy`, `sortOrder`: Tiêu chí sắp xếp, chẳng hạn theo ngày nộp bài

Thư viện `arxiv.py` cho phép tương tác linh hoạt với API này [1][2][3]. Có hai lưu ý thực tế cho năm nay: quy tắc ứng xử của API yêu cầu tốc độ gửi yêu cầu ở mức vừa phải — chờ ít nhất 3 giây giữa các lần gọi liên tiếp, trên một kết nối tại một thời điểm [4] (client dùng bên dưới tự động áp dụng độ trễ này); và `arxiv.py` đã trải qua các thay đổi gây phá vỡ tương thích (breaking changes) trong các bản phát hành 2.x–4.x (2025–2026), trong đó các mẫu cũ `search.results()` và `Result.download_pdf()` / `Result.download_source()` đã bị loại bỏ ở phiên bản 4.0.0 (tháng 5/2026) — hãy luôn dùng kiểu hiện đại `client.results(search)` và tham khảo README của thư viện để biết hành vi hiện tại.

**Ví dụ hàm truy vấn arXiv API:**

```python
import arxiv

def find_papers(keyword, limit=5):
    search = arxiv.Search(query=f"all:{keyword}",
                          max_results=limit,
                          sort_by=arxiv.SortCriterion.SubmittedDate)
    return [paper.title for paper in arxiv.Client().results(search)]

# Ví dụ sử dụng
titles = find_papers("quantum computing")
print(titles)
```

Kết quả được trả về dưới dạng các đối tượng có cấu trúc chứa siêu dữ liệu như tiêu đề, tác giả, tóm tắt và arXiv ID. Có hai thông tin về API quan trọng đối với phạm vi được giao. Thứ nhất, một lần tra cứu `id_list` với ID gốc (base ID) sẽ trả về bài báo với định danh kết thúc bằng hậu tố của phiên bản _mới nhất_ (ví dụ `2310.12345v4` cho bản chỉnh sửa thứ tư), do đó đọc hậu tố này cho bạn biết bài báo có bao nhiêu phiên bản — điều này quyết định yêu cầu "mọi phiên bản" bên dưới. Thứ hai, từng phiên bản riêng lẻ được tải về qua endpoint `/src/` có kèm phiên bản, được mô tả trong phần tải xuống.

#### OAI-PMH để thu hoạch siêu dữ liệu

Giao thức Open Archives Initiative Protocol for Metadata Harvesting (OAI-PMH) cho phép truy xuất siêu dữ liệu từ arXiv một cách có hệ thống. Đây là giao thức truy cập hàng loạt được hỗ trợ chính thức cho các kho lưu trữ học thuật và rất phù hợp cho việc thu thập siêu dữ liệu tự động. Từ tháng 3/2025, endpoint của arXiv nằm tại [https://oaipmh.arxiv.org/oai](https://oaipmh.arxiv.org/oai); địa chỉ cũ `export.arxiv.org/oai2` đã bị ngừng trong quá trình arXiv chuyển sang đám mây và có thể chỉ còn chuyển hướng, vì vậy hãy trỏ các trình thu hoạch mới vào endpoint mới [5][6].

**Các khái niệm chính** — OAI-PMH sử dụng một tập các "động từ" (verb – lệnh) để yêu cầu dữ liệu từ kho:

- `Identify`: Lấy thông tin về kho lưu trữ
- `ListRecords`: Truy xuất nhiều bản ghi kèm siêu dữ liệu
- `ListIdentifiers`: Chỉ truy xuất các định danh (ID)

**Thu hoạch có chọn lọc** — Việc thu hoạch có thể được giới hạn theo khoảng thời gian (`from`/`until`), theo danh mục (ví dụ `set=cs`) và theo định dạng siêu dữ liệu — tiền tố `arXiv` được khuyến nghị. Một thay đổi trong hành vi của bản viết lại tháng 3/2025 có ý nghĩa thực tế: các resumption token không còn mã hóa kích thước của tập dữ liệu và _hết hạn hằng ngày_, vì vậy hãy thiết kế một đợt quét toàn bộ sao cho hoàn tất trong vòng một ngày hoặc có thể tiếp tục một cách linh hoạt từ trạng thái đã lưu. Độ trễ 3 giây tương tự cũng áp dụng ở đây.

**Hàm truy xuất OAI-PMH cơ bản (endpoint cập nhật tháng 3/2025):**

```python
from sickle import Sickle

def harvest_arxiv_records(date_from, date_to):
    sickle = Sickle("https://oaipmh.arxiv.org/oai")
    records = sickle.ListRecords(metadataPrefix='arXiv',
                                 from_=date_from, until=date_to)
    return [r.metadata for r in records]
```

Ví dụ này cho thấy cách thu được một danh sách có cấu trúc gồm các từ điển siêu dữ liệu, sau đó có thể được lưu dưới dạng JSON hoặc trong cơ sở dữ liệu. Các resumption token xử lý việc phân trang tự động — các đợt thu hoạch hàng nghìn bản ghi chạy mà không cần quản lý trang thủ công, và đối với các đợt quét lớn theo khoảng thời gian, giao thức này vượt xa API về hiệu suất [5].

#### Bộ dữ liệu Kaggle cho siêu dữ liệu tổng hợp sẵn

Bộ dữ liệu arXiv của Cornell trên Kaggle ([https://www.kaggle.com/datasets/Cornell-University/arxiv](https://www.kaggle.com/datasets/Cornell-University/arxiv)) cung cấp siêu dữ liệu đã được thu thập sẵn cho hơn hai triệu bài báo. Có thể sử dụng trực tiếp mà không cần gọi API, rất lý tưởng cho việc xử lý cục bộ hoặc tìm kiếm khám phá [7].

**Xử lý dữ liệu và ví dụ** — Bộ dữ liệu được cung cấp ở định dạng JSON lines, trong đó mỗi dòng là siêu dữ liệu của một bài báo. Có thể dễ dàng nạp vào Python để lọc và phân tích bằng pandas:

**Đọc và lọc siêu dữ liệu arXiv qua bộ dữ liệu Kaggle:**

```python
import pandas as pd
import json

def load_arxiv_metadata(file_path, keyword):
    with open(file_path, 'r') as f:
        records = [json.loads(line) for line in f]
    df = pd.DataFrame(records)
    return df[df['title'].str.contains(keyword, case=False)]
```

Sau khi nạp, sinh viên có thể trích xuất các trường cần thiết như tiêu đề, tóm tắt, tác giả hoặc danh mục cho tập con bài báo đã chọn.

#### Tóm tắt

| **Phương pháp**   | **Loại giao diện**     | **Công cụ / thư viện ví dụ** |
| ----------------- | ---------------------- | ---------------------------- |
| arXiv API         | Endpoint HTTP/XML      | arxiv.py                     |
| OAI-PMH           | Giao thức siêu dữ liệu | Sickle                       |
| Bộ dữ liệu Kaggle | Bộ dữ liệu JSON tĩnh   | pandas                       |

_Tóm tắt các phương pháp khám phá bài báo_

### Tải xuống mã nguồn toàn văn (Full-Text Source Download)

Sau khi đã xác định danh sách bài báo cần thu thập, bước tiếp theo là lấy các file mã nguồn đầy đủ của chúng. Mục tiêu là truy xuất mã nguồn TeX gốc đã nộp, dưới dạng lưu trữ `.tar.gz` hoặc các dạng khác, mà không sửa đổi nội dung. Lưu ý rằng có nhiều loại nguồn khác ngoài `.tar.gz`, và bạn sẽ cần xác định loại nào chứa các file `.tex` hoặc `.bib` — hai loại file duy nhất được giữ lại trong kho ngữ liệu làm việc (xem phần Yêu cầu bên dưới). Các dạng khác như file PDF hoặc tài liệu Word `.docx` có thể không chuyển đổi được thành file TeX; đối với các phiên bản như vậy, hãy giữ một bản ghi rỗng trong kho ngữ liệu thay vì bỏ qua công bố đó.

arXiv cung cấp file mã nguồn cho khoảng 97% số bài nộp — phần còn lại chỉ có PDF [8][9]. Một gói mã nguồn là một file tar nén gzip chứa các file `.tex` (thường là nhiều file, liên kết với nhau qua `\input` hoặc `\include`), các file thư mục tham khảo `.bib`, các file style và class, đôi khi có file `.bbl` đã biên dịch sẵn, và các file nhị phân của hình ảnh. Các gói mã nguồn được phục vụ bởi máy chủ mirror chuyên dụng `export.arxiv.org`: endpoint ưu tiên `https://export.arxiv.org/src/{arxiv_id}` luôn trả về một gói tar nén gzip, và nó cũng chấp nhận các định danh _có phiên bản_ (ví dụ `.../src/2310.12345v2`), đây chính là cách từng bản chỉnh sửa của một công bố được truy xuất riêng lẻ [10]. Hãy trỏ các lượt tải tự động vào mirror này và không bao giờ vào trang chính `arxiv.org` (crawl dồn dập ở đó sẽ kích hoạt hệ thống phát hiện robot tự động và bị chặn 403), đồng thời sử dụng header User-Agent mô tả rõ ràng với cùng độ trễ 3 giây.

#### Tải bằng arXiv API với Python

Cách đơn giản và phổ biến nhất để tải gói mã nguồn đầy đủ của một bài báo là thông qua thư viện Python `arxiv.py`, thư viện này đóng gói API thành các phương thức thân thiện với người dùng.

**Quy trình cơ bản**

1. Truy vấn API bằng arXiv ID của bài báo để lấy siêu dữ liệu.
2. Tải gói mã nguồn từ endpoint `/src/` cho từng bản chỉnh sửa được xác định thông qua lần tra cứu API — arxiv.py 4.x không còn cung cấp phương thức tải xuống, vì vậy bạn cần tự tải gói (ví dụ bằng `urllib`) [3].
3. Lưu gói vào thư mục của phiên bản đó.

**Tải file mã nguồn arXiv bằng arxiv.py (4.x):**

```python
import arxiv
import urllib.request

def get_source(arxiv_id, save_dir="./sources"):
    paper = next(arxiv.Client()
                     .results(arxiv.Search(id_list=[arxiv_id])))
    print(paper.title, paper.id)  # id kết thúc bằng hậu tố phiên bản mới nhất, ví dụ 2310.12345v4
    url = f"https://export.arxiv.org/src/{arxiv_id}"
    urllib.request.urlretrieve(url, f"{save_dir}/{arxiv_id}.tar.gz")

# Ví dụ sử dụng: mỗi phiên bản một lần gọi
get_source("2310.12345v1")
get_source("2310.12345v2")
```

#### Tải bằng dòng lệnh với arxiv-downloader

Đối với người dùng thích dùng lệnh trực tiếp hơn là viết script, tiện ích dòng lệnh mã nguồn mở `arxiv-downloader` cung cấp cách nhanh chóng để lấy cả PDF và mã nguồn. Được cài đặt qua pip, nó tải file mã nguồn chỉ bằng ID hoặc URL của bài báo [11].

**Ví dụ sử dụng**

```bash
arxiv-downloader 2302.06544 -s -d ./downloads
```

Cờ `-s` đảm bảo file mã nguồn được tải (không phải PDF), và `-d` chỉ định thư mục đầu ra.

#### Truy cập hàng loạt qua Amazon S3

arXiv lưu trữ các gói mã nguồn đầy đủ trong một bucket Amazon Web Services (AWS) S3 theo cơ chế requester-pays (người yêu cầu trả phí) tại `s3://arxiv/src/`. Giao diện này dành cho việc truy xuất quy mô lớn, cho phép tải có cấu trúc các bó `.tar` chứa nhiều gói bài báo theo từng tháng [12].

**Thiết lập và lệnh ví dụ** — Trước khi tải, hãy cấu hình thông tin xác thực AWS bằng `aws configure`. Sau đó dùng:

```bash
aws s3 cp s3://arxiv/src/arXiv_src_2401_001.tar \
              ./bulk/ --request-payer requester
```

Mỗi gói lưu trữ chứa nhiều file mã nguồn bài nộp được nhóm theo ngày tải lên. Các phần theo tháng tuân theo mẫu đặt tên `arXiv_src_YYMM_NNN.tar`, và toàn bộ kho lưu trữ có dung lượng cỡ vài TB và vẫn đang tăng, vì vậy hãy tham khảo `arXiv_src_manifest.xml` để biết danh sách các phần chính xác trước khi tải. Bạn có thể lặp quy trình này để tải các gói của các tháng liên tiếp khi cần. Ngoài ra, `s3cmd` cung cấp một giao diện nhẹ: `s3cmd get --recursive --requester-pays s3://arxiv/src/` truy xuất tất cả các gói mã nguồn hiện có mà không cần lặp thủ công. (Có một bản mirror miễn phí của cùng các nguồn này trên Google Cloud Storage tại `gs://arxiv-dataset`.)

#### Tóm tắt

| **Phương pháp** | **Giao diện truy cập**    | **Công cụ ví dụ** |
| --------------- | ------------------------- | ----------------- |
| arXiv API       | REST qua client Python    | arxiv.py          |
| Dòng lệnh       | Tiện ích CLI              | arxiv-downloader  |
| Amazon S3       | Lưu trữ đám mây hàng loạt | awscli hoặc s3cmd |

_Các phương pháp có sẵn để truy xuất file mã nguồn arXiv_

### Trích xuất tài liệu tham khảo (Extracting References)

Khi làm việc với các bài báo arXiv, việc trích xuất thông tin tham khảo có cấu trúc là thiết yếu để hiểu mối quan hệ trích dẫn. Phần này giới thiệu Semantic Scholar API như một phương pháp để truy xuất các tài liệu tham khảo và các định danh liên quan như arXiv ID mà không cần phân tích trực tiếp LaTeX.

#### Semantic Scholar

Semantic Scholar, do Allen Institute for AI phát triển, lập chỉ mục hơn 214 triệu bài báo và cung cấp truy cập có cấu trúc đến dữ liệu trích dẫn thông qua một API miễn phí [13][14]. Nó đặc biệt mạnh trong việc liên kết các bài báo arXiv với đồ thị học thuật rộng hơn, cung cấp các trường như tiêu đề, tác giả, năm và các định danh bên ngoài. Endpoint của API là `https://api.semanticscholar.org/graph/v1/paper/arXiv:{arxiv_id}`, trong đó các truy vấn dùng tiền tố arXiv để nhận diện.

**Các trường yêu cầu và phản hồi** — Để lấy các tài liệu tham khảo, hãy chỉ định các trường như `references`, `references.externalIds`, `references.title` và `references.year` trong các tham số truy vấn. Phản hồi là một đối tượng JSON chứa một mảng các tài liệu tham khảo, mỗi mục có thể có arXiv ID trong trường `externalIds.ArXiv`. Một hàm đơn giản để truy vấn và trích xuất dữ liệu tham khảo cơ bản được trình bày dưới đây:

**Truy vấn Semantic Scholar để lấy tài liệu tham khảo:**

```python
import requests

def get_paper_references(arxiv_id):
    url = f"https://api.semanticscholar.org/graph/v1/paper/arXiv:{arxiv_id}"
    params = {"fields": "references, references.externalIds, references.title"}
    response = requests.get(url, params=params)
    data = response.json()
    return data.get("references", [])
```

Hàm này truy xuất danh sách tài liệu tham khảo cho một arXiv ID cho trước.

#### Trích xuất arXiv ID

Từ phản hồi của API, các arXiv ID nằm trong từ điển `externalIds` của mỗi tài liệu tham khảo. Quy trình gồm lặp qua mảng references và kiểm tra sự tồn tại của khóa `ArXiv`.

**Xử lý các định danh có vấn đề** — Không phải tài liệu tham khảo nào cũng có arXiv ID, vì một số trích dẫn bài báo tạp chí, sách hoặc các nguồn không thuộc arXiv. Độ phủ cao hơn ở các lĩnh vực như khoa học máy tính, nơi arXiv rất phổ biến. Bên cạnh đó, các công bố trên arXiv trước năm 2007 còn dùng một sơ đồ đánh chỉ mục khác (định dạng cũ `archive/YYMMNNN`, ví dụ `hep-th/9901001`), do đó sinh viên cần giữ nguyên các định danh gốc cho những công bố này.

#### Giới hạn tốc độ và cách sử dụng

API áp dụng giới hạn 1 yêu cầu mỗi giây và 100 yêu cầu trong mỗi cửa sổ 5 phút đối với việc sử dụng không xác thực [13]. Vượt quá các giới hạn này sẽ gây ra lỗi 429, vì vậy hãy triển khai độ trễ và xử lý lỗi. Việc dùng khóa API (API key) được cho phép và sẽ giúp cải thiện hiệu năng chạy: bậc miễn phí dùng chung một nhóm yêu cầu không xác thực và có thể bị điều tiết (throttle) khi sử dụng nhiều, trong khi một khóa API (miễn phí) đảm bảo ổn định 1 yêu cầu/giây, với các giới hạn cao hơn có thể được cấp sau khi xét duyệt.

**Các lưu ý về độ chính xác** — Semantic Scholar đạt độ chính xác (precision) trên 90% trong việc trích xuất tài liệu tham khảo thông qua phân tích đa nguồn, bao gồm PDF và LaTeX khi có [15]. Tuy nhiên, các bài mới tải lên có thể bị chậm lập chỉ mục từ 1 đến 7 ngày, và sự khác biệt giữa các phiên bản có thể không được phân biệt.

**Hạn chế** — API có thể thiếu dữ liệu đối với các bài báo rất mới, nội dung không phải tiếng Anh hoặc các định dạng không thông thường. Trong những trường hợp đó, hãy đối chiếu với các file mã nguồn.

## Yêu cầu

Trong milestone này, sinh viên được Giảng viên Thực hành cung cấp một dải arXiv ID của các bài báo và phải thu thập các bài báo trong dải đó bằng chương trình. Như phần khám phá đã nêu, công việc là xác định các ID được giao và tìm mọi phiên bản của từng bài báo — đây là phần được chấm điểm của milestone — chứ không phải thu hoạch arXiv ở quy mô lớn. Với mỗi bài báo được giao, **phải thu thập tất cả các phiên bản có sẵn**, bao gồm bản nộp ban đầu và mọi bản chỉnh sửa sau đó. ID phiên bản arXiv tuân theo quy ước thêm hậu tố "v<số phiên bản>" vào ID gốc (ví dụ "2310.12345v1", "2310.12345v2"), và với các bài chỉ nộp một lần thì chỉ cần dùng "v1". Mỗi phiên bản được truy xuất riêng lẻ thông qua endpoint `/src/` có phiên bản đã giới thiệu ở trên.

Quá trình thu thập cần truy xuất các file mã nguồn toàn văn (TeX), siêu dữ liệu, các tham khảo BibTeX và thông tin tham khảo đã crawl cho từng phiên bản, đảm bảo bao phủ toàn diện các bài báo được giao. Vì kích thước đầy đủ của mỗi bài báo có thể rất lớn do các hình ảnh đi kèm, bạn được khuyến nghị **LOẠI BỎ mọi hình ảnh** khỏi dữ liệu đã thu thập vì chúng ta sẽ không cần đến (cho công việc sau này của bạn ;-)). Hãy loại bỏ bằng cách trích xuất, không phải bằng cách chỉnh sửa: chỉ trích xuất các file `.tex` và `.bib` từ mỗi gói mã nguồn — các file nhị phân hình ảnh và các file không phải mã nguồn khác không bao giờ được đưa vào kho ngữ liệu — và giữ nguyên các lệnh LaTeX định nghĩa hình ảnh (ví dụ các môi trường `figure`, các dòng `\includegraphics`) bên trong các file TeX, vì Milestone 2 phân tích cấu trúc tài liệu chứ không phải các file nhị phân của hình ảnh.

Ngoài ra, siêu dữ liệu tham khảo phải được crawl cho từng công bố thông qua Semantic Scholar API. Milestone này **không có nộp dữ liệu**: kho ngữ liệu được giữ lại trong nhóm làm đầu vào làm việc của Milestone 2 và 3, và sau này trở thành một phần của bản nộp dữ liệu cuối cùng trên Hugging Face — hai bảng của milestone này được quy định bên dưới, và các milestone sau sẽ lần lượt bổ sung các bảng của mình.

**Gợi ý tổ chức dữ liệu.** Các nhóm tự tổ chức dữ liệu làm việc của mình; quy ước sau đây chỉ là gợi ý — có thể làm theo nguyên văn hoặc điều chỉnh cho phù hợp nhất với nhóm. Giữ một thư mục làm việc cho mỗi công bố (ví dụ `2310-12345` cho ID `2310.12345`) chứa các file `.tex` và `.bib` của mọi phiên bản và, với mỗi phiên bản, siêu dữ liệu bài báo và siêu dữ liệu tham khảo đã crawl của nó: tiêu đề, tác giả, ngày nộp và ngày chỉnh sửa, nơi công bố (venue) khi có, và với mỗi tham khảo đã crawl thì có tiêu đề, tác giả, ngày nộp, arXiv ID nếu có, và Semantic Scholar ID như API trả về. Hãy giữ _toàn bộ_ các tham khảo đã crawl ở máy cục bộ — ở bản nộp cuối cùng, chỉ những tham khảo có arXiv ID mới trở thành các dòng của bảng `references` được công bố, vì bảng đó dùng arXiv ID làm khóa và đóng vai trò là tập đối sánh (matching pool) cho Milestone 3.

Ở bản nộp cuối cùng (Milestone 3), dữ liệu làm việc sẽ được chuyển thành các bảng của bộ dữ liệu được công bố. Milestone này đã quy định sẵn hai trong số đó:

| **Bảng**     | **Các cột (kiểu dữ liệu)**                                                                                                                                                                |
| ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `papers`     | arxiv_id (string, không null); title (string); authors (danh sách string); submission_date (chuỗi ISO); revised_dates (danh sách string); venue (string, có thể null)                     |
| `references` | arxiv_id (string, không null, bài báo trích dẫn); ref_arxiv_id (string, không null); ref_title (string); ref_authors (danh sách string); ref_submission_date (string); ref_s2_id (string) |

_Các bảng của bộ dữ liệu được quy định trong milestone này;_ `references` _chứa một dòng cho mỗi cặp trích dẫn–được trích dẫn_

Milestone 2 phản chiếu gợi ý này cho dữ liệu đã phân tích cú pháp và chuẩn hóa mà nó tạo ra, và định dạng nộp bài của Milestone 3 liệt kê tất cả các bảng được công bố cạnh nhau.

### Định dạng nộp bài

#### Mã nguồn

Nhóm duy trì một repository GitHub công khai duy nhất cho toàn bộ dự án: mỗi milestone commit mã nguồn của mình vào đó theo kiểu tích lũy, nhờ vậy lịch sử commit ghi lại tiến trình của nhóm theo thời gian. Repository chỉ chứa các file mã nguồn như file Python (`.py` hoặc `.ipynb`) — không bao gồm file dữ liệu, file thực thi đã biên dịch hay bất kỳ tài liệu không phải mã nguồn nào — cùng với:

```text
<group-repo>/
|-- src # thư mục chứa mã nguồn
|   |-- *.{py/ipynb}
|   |-- requirements.txt # Để tái tạo môi trường
|-- README.md # Hướng dẫn thiết lập môi trường và chạy mã
```

Milestone này được nộp qua Moodle theo nhóm bằng cách dán các liên kết công khai đến repository mã nguồn dùng chung và đến báo cáo milestone (xem bên dưới). Mọi bài nộp ở trên phải sử dụng ngôn ngữ chính thức của chương trình học của bạn. **KHÔNG chấp nhận nộp trễ**.

#### Yêu cầu báo cáo (Jupyter Book)

Báo cáo milestone là một **Jupyter Book** được duy trì trong một repository công khai riêng — tách biệt với repository mã nguồn dùng chung — và được lưu trữ trên GitHub Pages của repository đó. Cuốn sách phát triển cùng dự án: báo cáo của milestone này là phần đầu tiên của nó, và Milestone 2 sẽ thêm một phần tiếp theo vào cùng cuốn sách. Cuốn sách phải trình bày các thống kê và phân tích về dữ liệu đã thu thập, và các hình và bảng của nó phải được _sinh ra trong quá trình biên dịch sách_ (không phải dán ảnh chụp màn hình tĩnh) nhằm đảm bảo chất lượng và tính tương tác. Báo cáo cần nêu chi tiết quá trình triển khai, bao gồm cách thức hoạt động của việc thu thập, các công cụ và thư viện đã dùng (ví dụ arXiv API, OAI-PMH, Semantic Scholar API, thư viện `requests`), và các thống kê liên quan như: số bài báo thu thập thành công, tỷ lệ thành công chung, kích thước trung bình của bài báo tính bằng byte trước và sau khi loại bỏ hình ảnh, số tham khảo trung bình mỗi bài báo, và tỷ lệ thành công trung bình khi thu thập siêu dữ liệu tham khảo.

Hiệu năng của bộ thu thập cũng cần được minh họa qua hai điểm chính: thời gian chạy (như tổng thời gian xử lý một bài báo, thời gian trung bình để xử lý mỗi bài báo được yêu cầu, tổng thời gian cho việc khám phá bài báo, v.v.) và mức sử dụng bộ nhớ (như lượng RAM tối đa dùng trong quá trình thu thập, mức tiêu thụ RAM trung bình, dung lượng lưu trữ đĩa tối đa cần thiết, dung lượng lưu trữ của đầu ra cuối cùng, v.v.). Hãy đo cả hai điểm theo cách có thể kiểm chứng — ví dụ, RAM đỉnh qua `/usr/bin/time -v` hoặc `psutil`, RAM trung bình qua việc lấy mẫu định kỳ, và mức sử dụng đĩa qua `du`; các công cụ này chỉ là ví dụ, không bắt buộc. Nếu hệ thống gồm nhiều chương trình, thời gian chạy được tính là thời gian thực (wall time) để chạy hệ thống theo kiểu đầu-cuối (end-to-end), còn mức sử dụng bộ nhớ được tính là tổng bộ nhớ mà toàn bộ hệ thống tiêu thụ. Môi trường kiểm thử cho bài đánh giá hiệu năng cuối cùng là một instance Google Colab chạy ở chế độ chỉ CPU. Không áp đặt giới hạn trên về thời gian chạy của bộ thu thập, còn giới hạn trên về dung lượng đĩa tối đa do instance Colab quyết định.

File `README.md` của repository được tách riêng: nó cung cấp hướng dẫn rõ ràng về việc thiết lập môi trường cần thiết (ví dụ phiên bản Python, các gói cần thiết qua pip hoặc conda) và chạy mã với các cấu hình cụ thể.

## Tiêu chí chấm điểm

Điểm của bạn được xác định dựa trên năm tiêu chí chính như liệt kê dưới đây:

- **Báo cáo (Jupyter Book):** Báo cáo có trình bày đầy đủ thông tin như đã nêu ở trên, với các hình và bảng được sinh ra trong quá trình biên dịch sách không?
- **Mã nguồn:** Mã có sạch, dễ đọc và chạy được không?
- **Khám phá & Thu thập:** Các phương pháp khám phá và tải xuống có được sử dụng phù hợp, với độ bao phủ đầy đủ các phiên bản của các bài báo được giao không?
- **Dữ liệu thu được:** Bộ thu thập đã crawl thành công ở mức độ nào, và kho ngữ liệu tuân theo các quy ước được gợi ý tốt đến đâu?
- **Hiệu năng của bộ thu thập:** Bộ thu thập hiệu quả như thế nào so với các nhóm khác?

## Tài liệu tham khảo

1. arXiv.org. (2007). _arXiv API User's Manual_. [https://info.arxiv.org/help/api/user-manual.html](https://info.arxiv.org/help/api/user-manual.html)
2. arXiv.org. (2007). _arXiv API Basics_. [https://info.arxiv.org/help/api/basics.html](https://info.arxiv.org/help/api/basics.html)
3. Schwab, L. (2015). _arxiv.py: Python wrapper for the arXiv API_. GitHub, [https://github.com/lukasschwab/arxiv.py](https://github.com/lukasschwab/arxiv.py)
4. arXiv.org. (1999). _Terms of Use for arXiv APIs_. [https://info.arxiv.org/help/api/tou.html](https://info.arxiv.org/help/api/tou.html)
5. arXiv.org. (1999). _Open Archives Initiative (OAI) - arXiv info_. [https://info.arxiv.org/help/oa/index.html](https://info.arxiv.org/help/oa/index.html)
6. arXiv API Discussion. (2025). _OAI-PMH Endpoint cutover_. [https://groups.google.com/a/arxiv.org/g/api/c/4Cm8bc7A6JM](https://groups.google.com/a/arxiv.org/g/api/c/4Cm8bc7A6JM)
7. Cornell University. (2025). _arXiv Dataset_. Kaggle, [https://www.kaggle.com/datasets/Cornell-University/arxiv](https://www.kaggle.com/datasets/Cornell-University/arxiv)
8. Ginev, D., & others. (2024). HTML papers on arXiv – why it is important, and how we made it happen. _arXiv Preprint arXiv:2402.08954_.
9. arXiv.org. (1999). _Why Submit the TeX/LaTeX Source?_ [https://info.arxiv.org/help/faq/whytex.html](https://info.arxiv.org/help/faq/whytex.html)
10. arXiv.org. (1999). _Media types - arXiv info_. [https://info.arxiv.org/help/mimetypes.html](https://info.arxiv.org/help/mimetypes.html)
11. Braun, S. (2023). _arxiv-downloader: Command-Line Tool for arXiv_. GitHub, [https://github.com/braun-steven/arxiv-downloader](https://github.com/braun-steven/arxiv-downloader)
12. arXiv.org. (2009). _arXiv Bulk Data Access - Amazon S3_. [https://info.arxiv.org/help/bulk_data_s3.html](https://info.arxiv.org/help/bulk_data_s3.html)
13. Allen Institute for AI. (2026). _Semantic Scholar Academic Graph API_. [https://api.semanticscholar.org/api-docs/](https://api.semanticscholar.org/api-docs/)
14. Kinney, R., Anastasiades, C., Authur, R., & others. (2023). The Semantic Scholar Open Data Platform. _arXiv Preprint arXiv:2301.10140_.
15. Ammar, W., & others. (2018). The Semantic Scholar Open Research Corpus. _arXiv Preprint arXiv:1805.02234_.
