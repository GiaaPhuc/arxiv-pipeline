# Project Milestone 3 — Pipeline Đối sánh Tham chiếu

> Bản dịch tiếng Việt từ: [https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/matching](https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/matching)
> Môn học: Nhập môn Khoa học Dữ liệu (Introduction to Data Science) — Dự án arXiv

## Mục tiêu

Trong cột mốc này, sinh viên áp dụng các kỹ thuật học máy vào một bài toán đối sánh thực tế: các mục trích dẫn được trích xuất từ văn bản đã phân tích được liên kết với siêu dữ liệu (metadata) bên ngoài của chúng. Cụ thể, các mục BibTeX được tạo ra ở Cột mốc 2 phải được đối sánh với đúng arXiv ID trong số siêu dữ liệu tham chiếu đã crawl ở Cột mốc 1 thông qua Semantic Scholar API [1, 2]. Bài thực hành này mô phỏng quy trình làm việc thực tế của khoa học dữ liệu về phân giải thực thể (entity resolution), trong đó xử lý văn bản phi cấu trúc, chuẩn hóa, kỹ thuật đặc trưng (feature engineering) và các thuật toán đối sánh được lắp ráp thành một pipeline hoàn chỉnh.

## Mô tả

Sinh viên phải thực hiện bài toán đối sánh giữa các mục BibTeX đã chuẩn hóa của Cột mốc 2, mà dạng thống nhất của chúng là bảng `bibtex` được mô tả ở đó, và siêu dữ liệu tham chiếu đã crawl của cùng các công bố đó, tức bảng `references` được mô tả ở Cột mốc 1. Mục tiêu là xác định, với mỗi mục BibTeX đã trích xuất, tham chiếu có khóa arXiv mà nó tương ứng. Toàn bộ pipeline khoa học dữ liệu được mô tả dưới đây.

### Làm sạch dữ liệu (Data Cleaning)

Thực hiện mọi bước tiền xử lý văn bản bổ sung cần thiết để hỗ trợ quá trình đối sánh, với các bước như chuyển về chữ thường, loại bỏ stop-word, hoặc cách tách token (tokenization) riêng cho tên tác giả và tiêu đề.

### Gán nhãn dữ liệu (Data Labelling)

Sinh viên cần tạo một tập dữ liệu chuẩn (ground truth) bằng cách gán nhãn các liên kết giữa các mục BibTeX đã trích xuất và các tham chiếu đã crawl của từng công bố. Nhiệm vụ là xác định, với mỗi mục BibTeX, tham chiếu mà nó tương ứng: một liên kết đúng duy nhất cho mỗi mục, và không có liên kết nào nếu không có tham chiếu đã crawl nào khớp. Các cặp chỉ tồn tại bên trong một công bố, nên một liên kết ghép một mục với một tham chiếu của cùng công bố. Sinh viên được yêu cầu:

- Gán nhãn thủ công các tham chiếu cho ít nhất **5 công bố**. Mỗi công bố được gán nhãn thủ công phải có ít nhất **20 mục BibTeX được gán nhãn**, số lượng của nó là $Q$, với tổng cộng ít nhất 100 mục được gán nhãn trên toàn bộ tập (pool).
- Cài đặt bộ đối sánh tự động bằng các công cụ cổ điển như regex hoặc các heuristic độ tương đồng chuỗi, và gán nhãn cho ít nhất **10%** số công bố nằm ngoài tập thủ công. Bộ đối sánh tự động gán nhãn cho mọi mục BibTeX của các công bố mà nó được áp dụng.

Các mục đã gán nhãn xác định ground truth phục vụ huấn luyện và đánh giá: với mỗi mục đã gán nhãn, dòng của nó trong ma trận ứng viên ở phần Mô hình hóa dữ liệu (Data Modeling) được hoàn thiện, gồm một giá trị dương (positive) khi tồn tại tham chiếu đúng và một dòng toàn giá trị âm (negative) trong trường hợp ngược lại. Định dạng nội bộ cho các nhãn này do sinh viên tự quyết định nhưng phải được ghi tài liệu rõ ràng trong kho mã của nhóm và được trình bày trên poster.

### Kỹ thuật đặc trưng (Feature Engineering)

Trước hết, sinh viên phải định hình bài toán đối sánh thành một bài toán học có giám sát cụ thể như phân loại (classification), hồi quy (regression), gợi ý (recommendation) hoặc xếp hạng (ranking); lựa chọn này quyết định định dạng đầu vào, chẳng hạn các cặp mục–tham chiếu, bộ ba (triplet) hoặc danh sách ứng viên. Dựa trên định dạng đầu vào đã chọn, sinh viên phải thực hiện kỹ thuật đặc trưng để xây dựng các đặc trưng hợp lý cho mô hình. Các đặc trưng này có thể từ đơn giản như điểm khớp chuỗi hoặc embedding đến phức tạp như các đặc trưng thống kê.

Sinh viên phải biện minh cho việc tạo ra từng đặc trưng trong poster và tài liệu của kho mã, giải thích ý tưởng nền tảng. Hiểu biết và ý tưởng này cần được minh họa bằng phân tích dữ liệu phù hợp.

**Lưu ý:** Trong giai đoạn kỹ thuật đặc trưng này, sinh viên được phép xây dựng các đặc trưng dựa trên các cấu trúc phân cấp đã hình thành ở Cột mốc 2.

### Mô hình hóa dữ liệu (Data Modeling)

Mục tiêu của bài toán là đối sánh các mục BibTeX đã chuẩn hóa ở Cột mốc 2 với các tham chiếu liên quan trong siêu dữ liệu đã crawl của cùng công bố.

Nếu một công bố có $m$ mục BibTeX và $n$ tham chiếu đã crawl có arXiv ID, thì không gian ứng viên của nó là một ma trận nhị phân $m \times n$: dòng $i$ chứa mục BibTeX $i$, cột $j$ chứa tham chiếu $j$, và ô $(i, j)$ mang một nhãn nhị phân có giá trị **yes** (có) khi mục $i$ tương ứng với tham chiếu $j$ và **no** (không) trong trường hợp còn lại. Việc gán nhãn được giới hạn theo công bố: các pool được chọn ở phần Gán nhãn dữ liệu cung cấp các công bố được gán nhãn, và ma trận của mỗi công bố được gán nhãn được hoàn thiện _theo từng dòng_, sao cho mỗi mục được gán nhãn có đúng một ô yes chứa tham chiếu đúng của nó khi tồn tại, trong khi các ô còn lại của dòng đó là no. Một mục không có tham chiếu đúng nào trong số các tham chiếu đã crawl sẽ tạo thành một dòng toàn giá trị no. Các mục chưa gán nhãn đơn giản là không có dòng nào.

Chiến lược chia dữ liệu như sau:

- **Tập kiểm tra (Test Set):** Gồm 1 công bố từ tập gán nhãn thủ công và 1 công bố từ tập được đối sánh tự động.
- **Tập kiểm định (Validation Set):** Gồm 1 công bố từ tập gán nhãn thủ công và 1 công bố từ tập được đối sánh tự động.
- **Tập huấn luyện (Training Set):** Gồm tất cả các công bố còn lại (các công bố gán nhãn thủ công còn lại và các công bố đối sánh tự động còn lại).

### Đánh giá mô hình (Model Evaluation)

Đánh giá mô hình đã huấn luyện trên **Tập kiểm tra**, gồm hai công bố đã định nghĩa ở trên. Với mỗi mục BibTeX được gán nhãn, mô hình xuất ra một danh sách xếp hạng gồm **5 tham chiếu ứng viên hàng đầu (top 5)**. Cho phép hậu xử lý kết quả.

Chỉ số đánh giá là **Mean Reciprocal Rank @5, viết là MRR@5**.

$$
\text{MRR@5} = \frac{1}{|Q|} \sum_{i=1}^{|Q|} \frac{1}{\text{rank}_i} \tag{1}
$$

trong đó $|Q|$ là số mục BibTeX được gán nhãn trong tập Test, cũng chính là đại lượng $Q$ như trong phần Gán nhãn dữ liệu, và $\text{rank}_i$ là vị trí của tham chiếu đúng trong 5 ứng viên hàng đầu của mục thứ $i$. Nếu tham chiếu đúng không xuất hiện trong năm ứng viên, điểm của mục đó là **không (0)**.

## Yêu cầu

Thực hiện toàn bộ pipeline, bao gồm làm sạch, gán nhãn, kỹ thuật đặc trưng, mô hình hóa và đánh giá, trên các công bố được giao. Kết quả đối sánh được công bố trong bảng `predictions` của bộ dữ liệu Hugging Face cuối cùng, được quy định trong phần Định dạng nộp bài. Bảng chứa một dòng cho mỗi mục BibTeX của mỗi công bố:

| **Cột**                        | **Kiểu**           | **Nội dung**                                                              |
| ------------------------------ | ------------------ | ------------------------------------------------------------------------- |
| arxivid                        | string, non-null   | Công bố mà mục thuộc về                                                   |
| partition                      | string, dictionary | `train`, `valid`, hoặc `test`                                             |
| citationkey                    | string, non-null   | Tên mục BibTeX, định danh của nó                                          |
| groundtrutharxivid             | string, nullable   | arXiv ID đúng của tham chiếu thực sự của mục, khi đã biết                 |
| cand1 đến cand5                | string, nullable   | Top-5 ứng viên được xếp hạng của mô hình: cand1 tốt nhất, cand5 cuối cùng |
| _Các cột của bảng predictions_ |                    |                                                                           |

**Lưu ý quan trọng:** Chỉ những công bố được dùng cho tác vụ đối sánh tham chiếu, tức nằm trong các tập Huấn luyện, Kiểm định và Kiểm tra, mới có dòng trong bảng này.

## Định dạng nộp bài

### Nộp dữ liệu cuối cùng (Hugging Face)

Trong dự án này **không có bước nộp dữ liệu trung gian**. Việc nộp dữ liệu cuối cùng diễn ra ở cột mốc này: dữ liệu đã xử lý đầy đủ được lưu trữ dưới dạng một **bộ dữ liệu Hugging Face công khai**, theo hướng dẫn lưu trữ do các Giảng viên Thực hành đưa ra, và liên kết đến bộ dữ liệu được dán qua Moodle.

**Định dạng lưu trữ: Parquet.** Công bố kho dữ liệu đã xử lý dưới dạng _Parquet phân mảnh (sharded Parquet)_, định dạng dữ liệu tiêu chuẩn của Hub mà trình xem (viewer) và backend streaming đọc trực tiếp, trong sáu bảng với một lược đồ (schema) thống nhất và tường minh. Giữ các cột ở dạng phẳng và nguyên thủy: các danh sách string một cấp cho tác giả và các ngày chỉnh sửa thì được, còn các đối tượng lồng nhau và các blob JSON đã tuần tự hóa thì không.

Các Cột mốc 1 và 2 đã quy định năm trong số các bảng này cùng với dữ liệu mà chúng tạo ra, và bảng predictions dưới đây hoàn thiện bộ bảng:

| **Bảng**                                                                | **Nội dung**                                                                                                                         | **Được mô tả ở** |
| ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ | ---------------- |
| papers                                                                  | siêu dữ liệu bài báo đã crawl                                                                                                        | Cột mốc 1        |
| references                                                              | siêu dữ liệu tham chiếu đã crawl, một dòng cho mỗi tham chiếu đã crawl và bài báo trích dẫn nó, chỉ giữ các tham chiếu có khóa arXiv | Cột mốc 1        |
| elements                                                                | các phần tử đã phân tích của mọi phiên bản, được loại trùng vào một kho duy nhất                                                     | Cột mốc 2        |
| hierarchy                                                               | các cạnh nút–cha theo từng phiên bản giữa các phần tử                                                                                | Cột mốc 2        |
| bibtex                                                                  | các mục BibTeX thống nhất sau khi loại trùng                                                                                         | Cột mốc 2        |
| predictions                                                             | kết quả đối sánh với các cột được nêu bên dưới                                                                                       | cột mốc này      |
| _Các bảng của bộ dữ liệu được công bố và nơi chứa đặc tả cột của chúng_ |                                                                                                                                      |                  |

Bản thân kho dữ liệu có cấu trúc đơn giản:

```text
└── <dataset-repo>/
    ├── README.md
    └── data/
        ├── papers/
        ├── references/
        ├── elements/
        ├── hierarchy/
        ├── bibtex/
        └── predictions/
```

Ở thư mục gốc, một **dataset card** (thẻ bộ dữ liệu), tức tệp `README.md` có siêu dữ liệu YAML, cho Hub biết các bảng ánh xạ tới các tệp như thế nào:

**Siêu dữ liệu dataset card (README.md):**

```text
---
configs:
- config_name: papers
  data_files:
  - split: train
    path: data/papers/*.parquet
- config_name: references
  data_files:
  - split: train
    path: data/references/*.parquet
- ...one config per table...
---
```

Cách tổ chức làm việc giữa các cột mốc do từng nhóm tự quyết định, và các Cột mốc 1 và 2 gợi ý thông tin nào nên giữ lại, vì các bảng này chỉ là định dạng công bố cho lần nộp cuối. Tránh dùng các script tải dữ liệu tùy chỉnh: các mẫu tên tệp và thẻ ở trên là tất cả những gì Hub cần. **KHÔNG chấp nhận nộp trễ**.

### Mã nguồn

Mã nguồn của cột mốc này được commit vào cùng một kho GitHub công khai duy nhất đã dùng ở các Cột mốc 1 và 2, tiếp nối lịch sử tích lũy ghi lại tiến độ của nhóm. Kho chỉ chứa các tệp mã nguồn như tệp Python có đuôi `.py` hoặc `.ipynb`, và loại trừ các tệp dữ liệu, tệp thực thi đã biên dịch cùng các tài liệu không phải mã nguồn khác, cùng với:

```text
<group-repo>/
|-- src # thư mục chứa mã nguồn
|   |-- *.{py/ipynb}
|   |-- requirements.txt # Để tái tạo môi trường
|-- README.md # Hướng dẫn cài đặt môi trường, chạy mã, tài liệu về đặc trưng và nhãn
```

Cột mốc này được nộp qua Moodle theo nhóm: tệp poster (xem bên dưới) và các liên kết công khai đến bộ dữ liệu Hugging Face và đến kho mã nguồn chung. Tất cả các bài nộp nói trên phải sử dụng ngôn ngữ chính thức của chương trình học của bạn.

### Poster và Buổi trình bày Poster

Hình thức chấm điểm cuối cùng là một **buổi trình bày poster (poster session)**. Mỗi nhóm nộp **một tệp pptx chứa poster một trang** theo khổ **A2 dọc (portrait)** để được duyệt. Poster nên trình bày những phần công việc được nhóm chọn lọc: những phần thể hiện _chất lượng cao nhất_, các kết quả _nổi bật nhất_, và công việc mà nhóm tin rằng làm nên _sự khác biệt so với các nhóm khác_. Ví dụ gồm các đặc trưng đã xây dựng cùng lời biện minh và phân tích, mô hình và kết quả MRR của nó, và các thống kê đáng chú ý của pipeline phân tích cú pháp và đối sánh.

Sau khi được duyệt, nhóm **in poster** và **trình bày tại buổi trình bày poster cuối kỳ**.

## Tiêu chí chấm điểm

Điểm của bạn được xác định dựa trên năm tiêu chí chính sau:

- **Triển khai pipeline ML:** Cài đặt đúng các bước làm sạch, gán nhãn, kỹ thuật đặc trưng và mô hình hóa cho bài toán đối sánh tham chiếu.
- **Đánh giá và phân tích:** Sử dụng MRR đúng cách, biện minh rõ ràng cho các đặc trưng, và phân tích sâu sắc các kết quả, như được trình bày trong poster và tài liệu của kho mã. Lưu ý rằng các sinh viên sử dụng các phương pháp dùng tri thức bên ngoài như LLM hoặc mô hình tiền huấn luyện (pretrained) sẽ được đánh giá tách riêng với các sinh viên khác.
- **Lược đồ bộ dữ liệu:** Bộ dữ liệu Parquet được công bố tuân thủ đặc tả về bảng và cột, sẵn sàng cho streaming.
- **Chất lượng mã và khả năng tái lập:** Mã sạch, chạy được, kèm hướng dẫn rõ ràng.
- **Poster và buổi trình bày:** Chất lượng của phần công việc được chọn, poster một trang A2 dọc, và phần trình bày tại buổi poster.

## Tài liệu tham khảo

1. Allen Institute for AI. (2026). _Semantic Scholar Academic Graph API_. [https://api.semanticscholar.org/api-docs/](https://api.semanticscholar.org/api-docs/)
2. Kinney, R., Anastasiades, C., Authur, R., & others. (2023). The Semantic Scholar Open Data Platform. _arXiv Preprint arXiv:2301.10140_.

---

_Điều hướng: ← Cột mốc 2 — Parsing_
