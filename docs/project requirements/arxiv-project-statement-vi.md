# Dự án arXiv — Đề bài

> Môn học: Nhập môn Khoa học Dữ liệu (Introduction to Data Science)
> Nguồn: https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/

Dự án nhóm của môn Nhập môn Khoa học Dữ liệu — thu thập dữ liệu (crawling), phân tích cú pháp phân cấp và chuẩn hóa, và pipeline đối sánh tài liệu tham khảo (reference matching).

## Quy định nộp bài (Logistics)

Dự án được thực hiện theo **nhóm**. Mỗi milestone được nộp theo nhóm qua Moodle:

- **Kho mã nguồn chung (shared code repository)** — mỗi nhóm có một kho mã công khai dùng cho toàn bộ dự án: mã của từng milestone được commit cộng dồn vào đó, nên lịch sử commit thể hiện tiến độ của nhóm. Milestone 1 và 2 dán đường dẫn kho mã qua Moodle cùng với đường dẫn báo cáo; Milestone 3 nộp kho mã cùng với poster và bộ dữ liệu.

- **Jupyter Book (Milestone 1 và 2)** — các báo cáo milestone tạo thành một **Jupyter Book** công khai, được host trên GitHub Pages của một kho riêng biệt: báo cáo Milestone 1 là phần đầu tiên của cuốn sách, và Milestone 2 bổ sung phần tiếp theo. Các hình và bảng trong báo cáo phải được sinh ra trong quá trình biên dịch (compile) cuốn sách.

- **Nộp dữ liệu cuối cùng** — không có đợt nộp dữ liệu trung gian. Dữ liệu đã xử lý hoàn chỉnh được host dưới dạng **dataset trên Hugging Face** gồm các bảng Parquet được chia shard; tập các bảng được quy định xuyên suốt các đề bài milestone (papers và references ở Milestone 1; elements, hierarchy và bibtex ở Milestone 2; predictions ở Milestone 3), và việc host tuân theo hướng dẫn của Giảng viên thực hành. Đường dẫn dataset được nộp qua Moodle.

- **Chấm điểm cuối kỳ** — theo hình thức **poster session**. Mỗi nhóm nộp một file pptx chứa một poster một trang theo khổ **A2 dọc (portrait)** để duyệt; khi được chấp nhận, nhóm in poster và trình bày tại buổi poster session.

## Các milestone

- **[Project Milestone 1 — Thu thập dữ liệu (Data Crawling)](https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/crawling)** — xây dựng kho dữ liệu thô: tìm các bài báo arXiv được giao trong khoảng do Giảng viên thực hành cung cấp, thu thập mọi phiên bản (version) của từng công bố (mã nguồn LaTeX toàn văn, metadata và metadata của tài liệu tham khảo), và phân tích kho dữ liệu đã thu thập.

- **[Project Milestone 2 — Phân tích cú pháp phân cấp và Chuẩn hóa (Hierarchical Parsing and Standardization)](https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/parsing)** — cấu trúc hóa kho dữ liệu: phân tích từng phiên bản thành một cây phân cấp, chuẩn hóa nội dung, chuyển các tham chiếu `\bibitem` sang BibTeX, loại bỏ trùng lặp giữa các phiên bản, và phân tích dữ liệu đã xử lý.

- **[Project Milestone 3 — Pipeline đối sánh tài liệu tham khảo (Reference Matching Pipeline)](https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/matching)** — chạy pipeline học máy: làm sạch dữ liệu, gán nhãn, xây dựng đặc trưng (feature engineering), huấn luyện mô hình và đánh giá một hệ thống đối sánh tài liệu tham khảo, được chấm điểm bằng Mean Reciprocal Rank 5 (MRR@5).

Yêu cầu cụ thể và định dạng nộp bài của từng milestone được trình bày chi tiết trong trang của milestone đó.

## Quy chế (Regulations)

Đảm bảo mã nguồn của bạn được viết chú thích rõ ràng. Bạn có thể dùng tài liệu trực tuyến để tham khảo, nhưng phải trích dẫn đầy đủ. Bạn được tự do thảo luận chủ đề với các bạn cùng lớp, nhưng bài làm phải được thực hiện và diễn giải theo hiểu biết của chính bạn.

**Bất kỳ milestone nào bị phát hiện có hành vi thiếu trung thực sẽ bị chấm 0 điểm.**
