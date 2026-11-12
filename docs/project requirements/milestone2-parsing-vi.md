# Project Milestone 2 — Phân tích Cú pháp Phân cấp và Chuẩn hóa

> Bản dịch tiếng Việt từ: [https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/parsing](https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/parsing)
> Môn học: Nhập môn Khoa học Dữ liệu (Introduction to Data Science) — Dự án arXiv

## Mục tiêu

Trong cột mốc này, sinh viên chuyển từ việc thu thập dữ liệu đơn giản sang xử lý và phân tích dữ liệu nâng cao. Trọng tâm là biến đổi dữ liệu thô không có cấu trúc, tức các mã nguồn LaTeX đã crawl được ở Cột mốc 1, thành một định dạng phân cấp có cấu trúc với nội dung đã được làm sạch, chuẩn hóa và loại bỏ trùng lặp. Bài thực hành này mô phỏng quy trình làm việc thực tế của khoa học dữ liệu: xử lý và chuẩn hóa văn bản phi cấu trúc. Phần học máy của quy trình, tức pipeline đối sánh tham chiếu (reference matching), sẽ nằm ở Cột mốc 3 và sử dụng các đầu ra được tạo ra ở đây.

## Mô tả

Sinh viên cần xây dựng một bộ phân tích cú pháp (parser) tự động để xử lý các tệp nguồn LaTeX đã thu thập từ Cột mốc trước. Pipeline xử lý gồm các thành phần sau:

### Thu thập nhiều tệp (Multi-file Gathering)

Nội dung của một mã nguồn LaTeX thường được trải ra trên nhiều tệp `.tex`, ví dụ thông qua các lệnh `\input` hoặc `\include`. Sinh viên phải xác định bằng chương trình đường biên dịch chính (main compilation path) và chỉ phân tích những tệp thực sự được đưa vào bản PDF cuối cùng. Các tệp không sử dụng hoặc dư thừa trong thư mục mã nguồn phải bị bỏ qua. Cụ thể, tệp gốc của công bố (publication root) là tệp `.tex` chứa khai báo `\documentclass`. Hãy lần theo các lệnh `\input` và `\include` một cách bắc cầu (transitively), đồng thời coi các lệnh bị comment như văn bản thông thường chứ không phải lệnh include; khi đó các tệp có thể đến được từ tệp gốc tạo thành đường biên dịch, còn mọi tệp khác trong thư mục mã nguồn là tệp phụ dư thừa. Việc lần theo đường này tái hiện đúng những gì LaTeX sẽ biên dịch, do đó kết quả phân tích bao phủ nội dung mà người đọc bản PDF cuối cùng nhìn thấy.

### Xây dựng cấu trúc phân cấp (Hierarchy Construction)

#### Cấu trúc phân cấp

Mục tiêu là chuyển mã LaTeX thô thành một cấu trúc cây phân cấp cho từng phiên bản của từng công bố.

- **Document** (Tài liệu) đóng vai trò là gốc.
- **Chapters** (Chương) hoặc **Sections** (Mục) thường tạo nên cấp thứ hai.
- Các thành phần khác như **Subsections** (Mục con), **Paragraphs** (Đoạn văn) và các phân chia logic tiếp theo tạo nên các cấp thấp hơn. Vì thứ tự lồng nhau chính xác phụ thuộc vào định dạng riêng của từng công bố, parser phải tự động xác định cấp phân cấp phù hợp cho mỗi thành phần như vậy.

#### Lưu ý khi phân tích

Để đảm bảo tính nhất quán, parser phải tuân thủ các quy tắc cụ thể về độ chi tiết của phần tử và các phần bị loại trừ:

- **Các phần tử nhỏ nhất:** Các nút lá của cây phân cấp phải là **Sentences** (Câu), tức văn bản được phân tách bằng dấu chấm; **Block Formulas** (Công thức khối), tức các khối toán học như nội dung của môi trường `equation` hoặc của `$$...$$`; và **Figures** (Hình), trong đó **Tables** (Bảng) cũng được coi là một loại Figure cho mục đích này. Các điểm liệt kê (itemized points) được coi là phân nhánh xuống các cấp sâu hơn, nghĩa là một khối `\begin{itemize}...\end{itemize}` được xem là một thành phần ở cấp cao hơn, và mỗi điểm trong đó là một phần tử ở cấp kế tiếp. Các nút lá câu mang nội dung văn xuôi của phần thân văn bản: không được tách câu bên trong công thức toán, các lệnh, các từ viết tắt như i.e., et al. và Fig. 1, các URL hay số thập phân, vì một dấu chấm không kết thúc câu thì không tạo ra một nút lá mới.
- **Loại trừ:** Các mục References (Tài liệu tham khảo) **không** được phân tích vào cây phân cấp.
- **Bao gồm:** Acknowledgements (Lời cảm ơn) và Appendices (Phụ lục), thường không đánh số thông qua dạng `\section`\*, **phải** được đưa vào.

### Chuẩn hóa và loại bỏ trùng lặp (Standardization and Deduplication)

#### Làm sạch và chuẩn hóa (Cleanup and Normalization)

Sinh viên phải làm sạch và chuẩn hóa dữ liệu trong cây phân cấp:

- **Làm sạch LaTeX:** Loại bỏ các lệnh định dạng không cần thiết, như `\centering`, tùy chọn vị trí `[htpb]` và `\midrule`, vì chúng không đóng góp vào ý nghĩa ngữ nghĩa.
- **Chuẩn hóa toán học:** Chuyển toàn bộ toán học nội dòng (inline) về một định dạng thống nhất, chẳng hạn dạng `$...$`, và chuyển toàn bộ toán học dạng khối về một định dạng thống nhất, chẳng hạn dạng `\begin{equation}...\end{equation}`.

#### Trích xuất tham chiếu (Reference Extraction)

Với các trích dẫn được định nghĩa bằng `\bibitem` trong các tệp `.tex`, sinh viên phải chuyển chúng thành các mục BibTeX chuẩn bằng các công cụ lập trình như biểu thức chính quy (regular expressions).

#### Loại bỏ trùng lặp (Deduplication)

Việc loại bỏ trùng lặp được yêu cầu ở hai mức, và cả hai mức đều hoạt động _xuyên suốt các phiên bản_ của một công bố:

- **Các mục tham chiếu (Reference Entries):** Các mục tham chiếu bị trùng lặp bên trong mỗi phiên bản hoặc giữa các phiên bản khác nhau của một công bố phải được xử lý thích hợp. Nếu các khóa trích dẫn (citation key, tức tên mục) khác nhau nhưng cùng chỉ đến một nội dung tham chiếu gốc, thì phải chọn một khóa trích dẫn duy nhất, và các lệnh `\cite{}` của những mục còn lại phải được đổi tên thành khóa được chọn này. Ngoài ra, quá trình loại trùng phải hợp nhất (union) các trường của các mục trùng lặp thay vì chọn một mục và bỏ đi toàn bộ thông tin còn lại. Xét một tham chiếu tồn tại qua ba phiên bản: phiên bản 1 liệt kê nó là `vaswani2017` với title, author và year; phiên bản 2 đổi tên nó thành `vit_transformer` và thêm `doi`; phiên bản 3 giữ cùng tham chiếu đó là `vit_transformer` nhưng bỏ `year`. Khi đó mục thống nhất giữ một khóa được chọn, hợp nhất các trường thành title, author, year và doi, và các lệnh `\cite` trỏ đến các khóa bị loại bỏ được đổi tên thành khóa đã chọn; đây chính là dòng sẽ nằm trong bảng `bibtex` bên dưới. Một ví dụ minh họa cho mức còn lại được trình bày cùng các bảng trong phần Yêu cầu, nơi một câu dùng chung phục vụ cả hai phiên bản như một phần tử duy nhất.
- **Nội dung văn bản đầy đủ (Full-text Content):** Phải áp dụng loại bỏ trùng lặp nội dung văn bản đầy đủ cho các phần tử phân cấp. Nếu nội dung văn bản của một phần tử khớp chính xác giữa các phiên bản khác nhau (khớp toàn văn sau khi làm sạch), nó phải được biểu diễn bằng một định danh duy nhất trong đầu ra cuối cùng, nhằm giảm dư thừa. Lưu ý: việc so khớp toàn văn nên được thực hiện _sau_ khi làm sạch để loại bỏ các khác biệt nhỏ về định dạng hoặc khoảng trắng, vốn có thể khiến mã nhận diện nhầm các văn bản giống hệt nhau là khác nhau.

## Yêu cầu

Xử lý mọi công bố được tạo ra ở Cột mốc 1. Với mỗi công bố, phân tích mã nguồn LaTeX của từng phiên bản theo đường biên dịch, xây dựng cấu trúc phân cấp, chuẩn hóa và loại trùng nội dung, đồng thời trích xuất các tham chiếu thành một tập mục BibTeX thống nhất. Một phiên bản không có mã nguồn TeX, được Cột mốc 1 giữ lại như một bản ghi rỗng, sẽ không cho ra phần tử nào: hãy ghi nhận phiên bản đó là rỗng thay vì bỏ qua, để các thống kê về độ bao phủ và các bảng bên dưới phản ánh đúng thực tế.

**Không có bước nộp dữ liệu** trong cột mốc này: kho dữ liệu đã xử lý được nhóm giữ lại làm đầu vào làm việc cho Cột mốc 3, giống như kho dữ liệu đã crawl ở Cột mốc 1.

**Gợi ý tổ chức dữ liệu.** Như ở Cột mốc 1, các nhóm tự tổ chức dữ liệu làm việc của mình. Gợi ý này áp dụng cùng tinh thần đó cho dữ liệu đã phân tích và chuẩn hóa, nên có thể làm theo nguyên trạng hoặc điều chỉnh cho thuận tiện. Với mỗi công bố, hãy giữ nội dung và cấu trúc đã phân tích của _tất cả_ các phiên bản cùng nhau: các phần tử đã phân tích, các quan hệ cha–con theo từng phiên bản giữa chúng, và các mục BibTeX thống nhất đã loại trùng. Phần này thay thế thư mục TeX thô từ Cột mốc 1 làm tài liệu đầu vào cho các bước sau, vì vậy hãy giữ đủ thông tin để lắp ráp các bảng cuối cùng mà không cần phân tích lại.

Tại lần nộp cuối ở Cột mốc 3, dữ liệu này sẽ trở thành các bảng của bộ dữ liệu được công bố. Cột mốc này đã xác định sẵn ba trong số các bảng đó:

| **Bảng**                                           | **Các cột (kiểu dữ liệu) và nội dung**                                                                                                                                                                                                                                                           |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| elements                                           | một dòng cho mỗi phần tử _phân biệt_ xuyên suốt tất cả các phiên bản của công bố: elementid (string, non-null), arxivid (string, non-null), kind (string, dictionary), content (string). Nội dung khớp chính xác giữa các phiên bản (khớp toàn văn sau khi làm sạch) chỉ chiếm một dòng duy nhất |
| hierarchy                                          | arxivid (string, non-null), version (int), elementid (string, non-null), parentid (string, non-null), với một dòng cho mỗi cạnh nút–cha của từng phiên bản                                                                                                                                       |
| bibtex                                             | arxivid (string, non-null), citationkey (string, non-null), entrytype (string, dictionary), title (string), authors (danh sách các string), year (int, nullable), doi (string), journal (string), url (string), chứa các mục BibTeX thống nhất sau khi loại trùng và hợp nhất                    |
| _Các bảng dữ liệu được quy định trong cột mốc này_ |                                                                                                                                                                                                                                                                                                  |

**Cách các elements và hierarchy được lưu trữ xuyên suốt các phiên bản.** Các phần tử của những phiên bản khác nhau dùng chung một kho lưu trữ: khi một nút lá hoặc một thành phần xuất hiện ở hai phiên bản với nội dung đã làm sạch giống hệt nhau, nó chỉ được ghi một lần, nên một `element_id` duy nhất phục vụ cho cả hai phiên bản; đó chính là việc loại trùng được yêu cầu ở trên. Khi đó các dòng của `hierarchy` chỉ chứa các cạnh nút–cha của từng phiên bản, và một phần tử do phiên bản trước đóng góp có thể đóng vai trò là một nút trong các cạnh của phiên bản sau, được gắn vào một nút cha chỉ tồn tại ở phiên bản sau đó. Các ID phần tử bắt đầu bằng khóa của công bố, theo sau là một phần phân biệt (discriminant) duy nhất bên trong công bố. Ví dụ thu nhỏ dưới đây, gồm một công bố có hai phiên bản và một câu không đổi ở cả hai, cho thấy các dòng kết quả:

| **elementid**                                                                            | **kind** | **content**                            |
| ---------------------------------------------------------------------------------------- | -------- | -------------------------------------- |
| 2310-12345#root1                                                                         | document | Version 1 root                         |
| 2310-12345#root2                                                                         | document | Version 2 root                         |
| 2310-12345#h1                                                                            | heading  | Introduction                           |
| 2310-12345#h2                                                                            | heading  | Background and model                   |
| 2310-12345#s1                                                                            | sentence | We study the citation matching problem |
| _Các dòng_ `elements` _mẫu, một kho dùng chung trong đó_ `s1` _phục vụ cả hai phiên bản_ |          |                                        |

| **version**                                                                                                                                      | **elementid** | **parentid**     |
| ------------------------------------------------------------------------------------------------------------------------------------------------ | ------------- | ---------------- |
| 1                                                                                                                                                | 2310-12345#h1 | 2310-12345#root1 |
| 1                                                                                                                                                | 2310-12345#s1 | 2310-12345#h1    |
| 2                                                                                                                                                | 2310-12345#h2 | 2310-12345#root2 |
| 2                                                                                                                                                | 2310-12345#s1 | 2310-12345#h2    |
| _Các dòng_ `hierarchy` _mẫu: câu dùng chung_ `s1` _là một nút ở cả hai phiên bản, được gắn vào_ `h1` _ở phiên bản 1 và vào_ `h2` _ở phiên bản 2_ |               |                  |

## Định dạng nộp bài

### Mã nguồn

Mã nguồn của cột mốc này được commit vào cùng một kho GitHub công khai duy nhất đã dùng ở Cột mốc 1, tiếp nối lịch sử tích lũy ghi lại tiến độ của nhóm. Kho chỉ chứa các tệp mã nguồn như tệp Python có đuôi `.py` hoặc `.ipynb`, và loại trừ các tệp dữ liệu, tệp thực thi đã biên dịch cùng các tài liệu không phải mã nguồn khác, cùng với:

```text
<group-repo>/
|-- src # thư mục chứa mã nguồn
|   |-- *.{py/ipynb}
|   |-- requirements.txt # Để tái tạo môi trường
|-- README.md # Hướng dẫn cài đặt môi trường và chạy mã
```

Cột mốc này được nộp qua Moodle theo nhóm bằng cách dán các liên kết công khai đến kho mã nguồn chung và đến báo cáo cột mốc được mô tả bên dưới. Tất cả các bài nộp nói trên phải sử dụng ngôn ngữ chính thức của chương trình học của bạn. **KHÔNG chấp nhận nộp trễ**.

### Yêu cầu báo cáo (Jupyter Book)

Sinh viên phải chuẩn bị một báo cáo cột mốc được biên dịch dưới dạng **Jupyter Book**, chính là kho sách đã giới thiệu ở Cột mốc 1 và được lưu trên GitHub Pages của nó, bằng cách thêm một phần về phân tích cú pháp và chuẩn hóa vào cuốn sách đó thay vì tạo một cuốn mới. Cuốn sách phải trình bày các thống kê và phân tích của dữ liệu đã xử lý, và các hình, bảng của nó phải được _tạo ra trong lúc biên dịch sách_, không dán dưới dạng ảnh chụp màn hình tĩnh, nhằm đảm bảo chất lượng và tính tương tác. Báo cáo cần bao gồm cụ thể:

- **Phân tích cú pháp phân cấp:** Giải thích logic dùng để phát hiện cấu trúc tệp, phân tích phân cấp LaTeX và xử lý đầu vào nhiều tệp.
- **Chuẩn hóa:** Chi tiết về cách thực hiện làm sạch dữ liệu, chuẩn hóa toán học và loại trùng tham chiếu.
- **Thống kê:** Các thống kê liên quan như tỷ lệ phân tích thành công, độ bao phủ phiên bản của từng công bố, và số lượng phần tử/mục trước và sau khi loại trùng.

Hiệu năng của parser cũng cần được minh họa qua hai điểm chính: thời gian chạy và dung lượng bộ nhớ. Thời gian chạy tính theo thời gian thực đầu–cuối (end-to-end wall time) của parser và thời gian trung bình để xử lý một công bố, còn dung lượng bộ nhớ tính theo lượng RAM tối đa được dùng và dung lượng lưu trữ của đầu ra cuối cùng. Môi trường kiểm thử cho benchmark cuối cùng là một instance Google Colab chạy ở chế độ chỉ CPU. Không áp đặt giới hạn trên về thời gian chạy, còn giới hạn trên về dung lượng đĩa do chính instance Colab quy định.

Tệp `README.md` của kho mã được giữ riêng: nó cung cấp hướng dẫn rõ ràng về cách thiết lập môi trường cần thiết, như phiên bản Python và các gói cần cài qua pip hoặc conda, và cách chạy mã với các cấu hình cụ thể.

## Tiêu chí chấm điểm

Điểm của bạn được xác định dựa trên năm tiêu chí chính sau:

- **Parser phân cấp:** Độ chính xác của parser trong việc phát hiện cấu trúc, xử lý mã nguồn nhiều tệp và xác định các cấp phân cấp phù hợp.
- **Chuẩn hóa và loại trùng:** Làm sạch đúng, chuẩn hóa toán học, trích xuất tham chiếu, và cả hai mức loại trùng, bao gồm các mục tham chiếu thông qua việc đổi tên `\cite` và hợp nhất trường, cũng như nội dung toàn văn xuyên suốt các phiên bản.
- **Hiệu năng của parser:** Mức độ hiệu quả của parser thông qua thời gian chạy và dung lượng bộ nhớ.
- **Chất lượng mã và khả năng tái lập:** Mã sạch, chạy được, kèm hướng dẫn rõ ràng.
- **Báo cáo (Jupyter Book):** Các hình được tạo lúc biên dịch sách, tính tương tác và sự rõ ràng của cuốn sách được lưu trữ.

---

_Điều hướng: ← Cột mốc 1 — Crawling | Cột mốc 3 — Matching →_
