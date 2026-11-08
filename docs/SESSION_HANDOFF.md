# SESSION HANDOFF — Quỳnh, phần C Milestone 2

> Checkpoint: **08/11/2026, 17:37 Asia/Saigon**. Tài liệu này thay thế `handoff.md`
> ở gốc cho việc tiếp tục task M2; file gốc là lịch sử bàn giao M1 ngày 22/10.
> **Đã xác minh** = đọc code/Git local hoặc kết quả tool trong phiên này.
> **Chưa kiểm chứng** = không được coi là hoàn thành. Lệnh minh họa không phải
> yêu cầu tự động chạy, commit/push hoặc crawl khi mở phiên mới.

## 1. Project Overview

- Người dùng: **Phạm Trường Quỳnh, vai C, nhóm 15**. Leader A: Gia Phúc.
- Code chung: https://github.com/GiaaPhuc/arxiv-pipeline ; báo cáo nhóm ở repo
  riêng https://github.com/GiaaPhuc/arxiv-book .
- M1: crawler thu mọi phiên bản trong 5 dải, tổng **25.000 base ID**, giữ nguồn,
  metadata và references S2; SQLite manifest, checkpoint/Drive, metrics và export.
  Nhóm chọn crawl chung một máy do A vận hành. Không suy từ code/pilot rằng
  đã crawl xong toàn bộ; số cuối và tình trạng nộp Moodle **chưa được xác minh**.
- M2: nguồn M1 → C gom file/dựng cây + B clean/math/split → D trích tham khảo,
  dedup/xuất Parquet → E validate/Book. A giữ schema, orchestration và profiler.
- M3: matching, ground truth và dữ liệu HF cá nhân. Q&A thầy: mỗi người gán nhãn
  references cho ít nhất 5 publication; pipeline dùng chung, cuối M3 tách dữ liệu
  theo ID range đưa lên HF từng người. Không triển khai M3 trong task này.
- Windows/PowerShell, Python **3.11**, pytest/responses, Ruff **0.17.1**.
  Interpreter: **`D:/Project_NMKHDL/.venv/Scripts/python.exe`**.
  Hai module C mới chỉ dùng thư viện chuẩn + schema M2; không thêm dependency.

## 2. Current Objectives

Người dùng đã yêu cầu triển khai đầy đủ kế hoạch phần C: **M2-C1/C2/C3**.
Code, test và kiểm chứng C độc lập đã làm; **M2-C vẫn 🟡** vì chưa tích hợp
B/D thật và đường `.bbl`. Không cần viết lại từ đầu hoặc làm thay B/D/E.

**Corpus đã xác nhận với người dùng:** leader cung cấp, người dùng đã giải nén
thành `arxiv-data/`. Kiểm kê metadata **1.000 bài / 1.678 phiên bản**: 1.526 `ok`,
148 `no_source`, 4 `not_found`; số thư mục phiên bản khớp `num_versions`. Không có
`.bbl`. Chưa đối chiếu gói nén gốc, validate toàn subset hay xác nhận hoàn tất 25k.

**Đã thử 10 bài / 26 phiên bản thật + 3 phiên bản đối chứng** bằng hook truyền nguyên
văn, không gọi mạng. 349 tệp mẫu khớp kích thước/SHA-256; kiểm tra root/cây/marker
References đạt. Đã sửa root nhầm template ở `2408.11049` v4/v5. C dùng **265/324 TeX**;
6 phiên bản mẫu cảnh báo bibliography thiếu trong subset. Chi tiết và danh sách
phối hợp B/D/A/E: `docs/reports/m2/02_c_real_sample_check.md` + JSON cùng thư mục.

- C1: gom nguồn theo đường biên dịch, chọn root, khử comment, chặn vòng lặp.
- C2: cây heading/paragraph/list/item/sentence/formula/figure, tách References.
- C3: ≥25 test, thử 10 bài thật và bản thảo phương pháp cho Book.
- Leader plan: module merge hết **12/11**, tích hợp/pilot/kiểm chứng **13/11**.
  Phiếu gọi 13/11 là hạn Moodle nhưng chưa đối chiếu Moodle trực tiếp. Q&A thầy
  cho phép sửa Book đến hạn poster, giữ URL. Đừng tự kết luận các mốc là một.
- Report: người dùng chọn **bản thảo giao E**, không sửa repo sách trực tiếp.
- Commit, push và tạo PR là bước bàn giao riêng, **chưa được thực hiện** cho M2.

## 3. Completed Work

Các file mới ở root checkout, còn **untracked**:

| File | Nội dung đã có |
| --- | --- |
| `src/parsing/gathering.py` | Bộ quét comment/literal, root scoring, DFS include, BibTeX/BBL, thống kê file |
| `src/parsing/hierarchy.py` | Stack heading, cây danh sách, lá formula/figure, hooks B, tách References, phục hồi lỗi cấu trúc |
| `src/tests/test_gathering.py` | Nguồn tổng hợp cho comment/root/include/tham khảo/path/decoding |
| `src/tests/test_hierarchy.py` | Cây, hợp đồng hooks, lỗi/giới hạn và tích hợp C thật qua `Stages` với B/D giả |
| `docs/reports/m2/01_parsing_hierarchy.md` | Bản thảo tiếng Việt cho E; ví dụ, thuật toán, bằng chứng và giới hạn |
| `docs/reports/m2/02_c_real_sample_check.md` | Bảng 26 phiên bản + 3 đối chứng, lỗi root đã sửa, giới hạn và đầu việc B/D/A/E; chưa gửi nhóm |
| `docs/reports/m2/c_real_sample_check.json` | Hash code/metadata, root, file/nút, đề mục đối chiếu và cảnh báo từng phiên bản |

Hai file tracked đã sửa: **`STATUS.md`**, **`docs/du-an-va-luong-xu-ly-hien-tai-vi.md`**.
Đã ghi số test, tiến độ C và khoảng thiếu `.bbl`; thêm phần 12 mô tả luồng M2 C.
Không sửa schema M1/M2, `corpus.py`, pipeline A, hoặc test của thành viên khác.

M1 của Quỳnh đã hoàn tất trước task này: fixture **PR #4**, source **PR #6**;
STATUS cũ được đồng bộ qua **PR #8**. Các đợt tối ưu source/HTTP sau đó thuộc main
leader, không phải lỗi cần quay lại viết lại code M1 của C.

## 4. Technical Decisions

**Giữ nguyên API theo schema và caller của A:**

```python
strip_comments(tex: str) -> str
elect_root_tex(tex_files: dict[str, str]) -> str | None
gather_compilation_unit(arxiv_id: str, version: int,
                        raw_files: dict[str, bytes]) -> CompilationUnit
build_raw_hierarchy(unit: CompilationUnit,
                    cleaner_hooks: CleanerHooks) -> tuple[list[RawNode], list[str]]
```

- File rỗng: root `None`, hierarchy trả **`([], [])`**, không tạo document giả.
  Gathering vẫn giữ tham khảo được truyền vào. `RawNode` dùng ID nguyên cục bộ,
  root `0`, cha tồn tại trước con; D mới cấp ID bảng cuối và dedup.
- C không import trực tiếp module B; caller truyền đủ 4 hàm `CleanerHooks`.
  FakeHooks trong test chỉ là contract double, không thay thuật toán B.
- Scanner trạng thái/ngoặc cân bằng bảo vệ verbatim/lstlisting/verb/URL và math
  khỏi bị hiểu là lệnh cấu trúc. Decode UTF-8 BOM rồi fallback latin-1.
- Root dùng công thức leader (begin document, class, includes, tên chuẩn,
  maketitle/abstract, độ dài); hòa điểm theo đường dẫn. Fallback begin document,
  rồi file lớn nhất. **Điều chỉnh 08/11:** chỉ thưởng include có đích tìm được trong
  mapping từ thư mục root ứng viên; giữ tín hiệu/trọng số khác và API. Đọc argument
  include dùng chung với DFS. Đây là heuristic, không phải compiler TeX.
- Include: root-relative trước current-relative; tự thêm `.tex`, xử lý input
  có/không ngoặc, include/subfile/import. Chỉ truy cập mapping đầu vào. Root sâu 0,
  tối đa 15 cạnh, active stack chống vòng; đếm unique file dù include lặp.
- Include thiếu/động/unsafe được log ID/version/path và bỏ riêng cạnh. Cảnh báo
  có thể đồng nghĩa coverage một phần; không gọi mọi kết quả là parse đầy đủ.
- `.bib` khai báo rõ được ưu tiên; không có khai báo thì giữ toàn bộ `.bib`
  đầu vào có cảnh báo. Thu `.bbl` liên quan và cả dạng bibitem không bọc env.
- Hierarchy: macro hook → math hook → phân cấu trúc → clean/split prose. Giữ
  Abstract/cảm ơn/phụ lục, References kết thúc ở heading cùng/cao cấp hoặc appendix.
  List/item lấy nội dung từ con đã phát ra để không rò bibliography qua nút cha.
- Env thiếu dấu đóng phục hồi tại heading tiếp theo/cuối tài liệu, có log.
  Cấu trúc sâu >64 báo ValueError; lỗi hook propagate để pipeline ghi lỗi.
- Không thực thi TeX, không đổi corpus, không gọi mạng trong unit test.

## 5. Current Git State

**Đã kiểm tra local trong lượt handoff:**

- CWD: **`D:/Project_NMKHDL`**, branch **`m2/c-gathering-hierarchy`**.
- HEAD: **`ac2c17462c1f3e02faec54840a549eb233331f9a`**, merge PR #23.
  Đây là base đã fetch trước triển khai, **không khẳng định là GitHub mới nhất
  tại lúc phiên mới chạy**. Handoff không fetch/pull hay đổi nhánh.
- Chưa có commit riêng cho M2, chưa push nhánh M2. Đừng suy `git diff --stat`
  chỉ có tài liệu là chưa viết code: bốn file Python mới vẫn **untracked**.
- Tracked modified: STATUS và tài liệu luồng ở mục 3.
- Untracked task: 7 file mới mục 3 + `docs/SESSION_HANDOFF.md` (tài liệu này).
- Untracked **có sẵn của người dùng**, không stage/dọn: `Plans/`, `handoff.md`,
  `milestone2-phan-cong-task-B-E-vi.md`, `project_requirements/`, `project_statement.html`.
- **`arxiv-data/` do leader cung cấp, untracked:** dữ liệu cần giữ ngoài commit, không
  stage, xóa hoặc di chuyển; chứa `papers/`, `logs/`, `manifest.sqlite`.

**Worktree cũ phải bảo toàn:**

| Đường dẫn sau `D:/Project_NMKHDL/` | Branch / HEAD | Tình trạng |
| --- | --- | --- |
| `.venv/worktrees/m1-c-fixtures-pr` | `m1/c-source` / `8ff05bb` | STATUS, source.py, test_source.py còn modified; implementation lịch sử |
| `.venv/worktrees/m1-c-fixtures-v2` | `m1/c-fixtures-v2` / `76ccb52` | Worktree fixture cũ |
| `.venv/worktrees/m1-c-source-v2` | `m1/c-source-v2` / `8183cc6` | Source trung gian |
| `.venv/worktrees/m1-c-source-v3` | `m1/c-status-update` / `636c67c` | Sạch tại kiểm tra handoff; tên thư mục không còn trùng tên branch |

`.venv` chứa cả interpreter, worktree và backup, **không được xóa/tạo lại như cache**.
Backup lịch sử có tại `.venv/handoff-backups/status-review-20261025/`.
Người dùng muốn giữ nhánh bàn giao/lịch sử; không tự xóa nhánh/worktree.

## 6. Pending Tasks (ưu tiên)

1. **B/D/A tích hợp module thật**: B cung cấp đủ 4 `CleanerHooks`; D trích tham khảo,
   dedup/export; A nối `default_stages()`. Checkout hiện chưa có thuật toán B/D;
   chưa đối chiếu remote, không suy thành các bạn chưa làm. Chạy lại đủ 26 phiên bản
   sau khi có code thật; lượt identity không chứng minh macro/math/split hoặc dedup.
2. **A/D xác minh bibliography và nối BBL**: `2408.11045` v1–v3 thiếu `references`,
   `2408.11053` v1 thiếu `ref`, `2408.11111` v1/v2 thiếu `CVs/Math/RB/mk` trong subset.
   Chưa rõ thiếu từ gói gốc hay lúc chuẩn bị subset. `Corpus.tex_files()` chỉ trả
   `.tex/.bib`; C hỗ trợ `.bbl` khi được truyền bytes, nhưng subset không có BBL.
   Cần kiểm tra đường end-to-end riêng; không tự sửa module D/A hoặc schema.
3. **C/E đối chiếu lại mẫu với B/D thật**: các ID đã lưu trong JSON. Kiểm tra
   references/cảm ơn/phụ lục, cảnh báo nguồn thiếu và các bảng Parquet/validator E.
   Không tải lại 25k; `data/papers` mặc định không có, dùng `arxiv-data` thực tế.
4. **E ghép bản thảo và build Book**. Bảng kiểm chứng mục 5 đã có nội dung bàn giao;
   chưa gửi tin nhóm, chưa sửa/build repo sách.
5. Bàn giao Git riêng khi người dùng giao: rà diff, chỉ stage file task, commit
   bằng danh tính Quỳnh, push branch, chuẩn bị PR. User thường tự Create PR/merge.

## 7. Known Issues / chưa kiểm chứng

- **Đã xác minh:** `arxiv-data` do leader cung cấp theo người dùng; 10 bài đã thử C
  độc lập, root v4/v5 đã sửa. Bibliography thiếu ở 6 phiên bản mẫu; subset không có
  `.bbl`, Corpus chưa truyền BBL. Book chưa được sửa/build cho bản thảo C.
- Bản thảo và tài liệu luồng đã đồng bộ kiểm chứng thật. Hook truyền nguyên văn
  khiến sentence bằng paragraph, không phải chất lượng tách câu/toán chuẩn của B.
  Kiểm tra References mới dựa marker/tên heading và đối chiếu mẫu, chưa bao phủ mọi macro.
- **Giới hạn thiết kế:** không thực thi điều kiện TeX/includeonly/catcode/package
  hoặc include tên động; root heuristic; latin-1 không bảo đảm đúng encoding tác giả;
  literal thiếu dấu đóng được giữ tới cuối. Cần ghi lỗi/coverage trung thực.
- **Lỗi môi trường test đã có cách chạy:** Temp sandbox Windows có `#` khiến URI
  SQLite của checkpoint bị cắt. Không sửa checkpoint/export để né lỗi; dùng Temp
  trong workspace (mục 8).
- **Chưa biết:** tính toàn vẹn toàn bộ 1.000 bài/gói gốc, chất lượng sau B/D thật,
  corpus 25k đã hoàn tất chưa, Moodle đã nộp gì, code mới các thành viên trên remote.
  Không suy từ kiểm hash mẫu hoặc metadata `ok` thành toàn bộ dữ liệu đạt.

## 8. Testing Status

**Kết quả lượt kiểm chứng/sửa root 08/11:**

- Ruff check đạt; Ruff format: **62 files left unchanged**.
- Thêm 6 trường hợp C: 5 regression template/include đều fail trước sửa; sau sửa
  **98 test C** đạt (**0,34 s**), toàn suite **359 passed, 27,06 s**, không skip.
- **26/26 phiên bản của 10 bài thật + 3/3 đối chứng** đạt kiểm tra với hook truyền
  nguyên văn; không còn cảnh báo include. Sáu phiên bản mẫu cảnh báo bibliography.
  JSON lưu root/file/nút/đề mục/cảnh báo và hash code/metadata. Đây không phải benchmark.
- Test tích hợp: C thật qua `Stages`/`process_paper`, corpus tổng hợp, B/D giả;
  gồm version có source và no_source, counts và chuyển bibliography.
- Lần full đầu: 7 checkpoint/export test lỗi URI Temp sandbox; chạy lại với
  TEMP/TMP workspace thì đạt. Không sửa các module/test đó.
- **Còn thiếu:** B/D thật, đường BBL qua Corpus, validator E/build Book, benchmark Colab.
- Lượt triển khai trước đạt 92 test C/353 test suite; đây là số lịch sử, không phải
  kết quả hiện tại. Không cần chạy lại chỉ để tái tạo số nếu không có thay đổi mới.

Lệnh kiểm tra khi có thay đổi mới (PowerShell, dùng basetemp tên mới mỗi lần):

```powershell
Set-Location D:/Project_NMKHDL
$taskTempRoot = 'D:/Project_NMKHDL/.venv/m2c-temp'
New-Item -ItemType Directory -Path $taskTempRoot -Force | Out-Null
$env:TEMP = $taskTempRoot
$env:TMP = $taskTempRoot
& D:/Project_NMKHDL/.venv/Scripts/python.exe -m ruff check .
& D:/Project_NMKHDL/.venv/Scripts/python.exe -m ruff format .
& D:/Project_NMKHDL/.venv/Scripts/python.exe -m pytest -q --basetemp=D:/Project_NMKHDL/.venv/test-m2c-NEXT-UNUSED
git diff --check
```

Không chọn basetemp chứa dữ liệu người dùng; pytest có thể xóa nội dung nó.
Không chạy lại chỉ để tái tạo số test nếu chưa có thay đổi hoặc yêu cầu kiểm tra.

## 9. Next Steps cho phiên mới

1. Đọc `AGENTS.md`, STATUS mục 0, handoff này; nguồn phân công chi tiết nằm ở
   `docs/plans/milestone2-phan-cong-task-B-E-vi.md` và bản leader gửi ở root.
   Actual schema/caller là nguồn API; không dùng tên hàm minh họa cũ trong plan.
2. Báo user mục đích/cwd/phạm vi trước lệnh. Theo sở thích user: gửi danh sách
   lệnh mới và đợi “duyệt/proceed” trước khi chạy; không tự mở rộng sang push/crawl.
   Read-only qua connector đã dùng để xem GitHub. Không gửi tin nhắn leader/nhóm
   nếu chưa được yêu cầu; chỉ soạn nội dung cho user chuyển tiếp.
3. Kiểm tra status/diff trước thao tác Git; bảo toàn untracked và worktree cũ.
   Nếu cần cập nhật remote: fetch để đối chiếu, không pull mù trên branch bẩn,
   không reset/stash/force-push hoặc `git add .`.
4. Đọc bảng kiểm chứng và JSON trước khi chạy lại: nguồn gốc đã được người dùng
   xác nhận, kiểm chứng C độc lập đã làm. Tiếp tục các gate B/D/BBL/E mục 6;
   không yêu cầu API key cho nguồn đã tải hoặc tự đánh dấu M2-C ✅.
5. Khi người dùng giao bàn giao: rà diff và bằng chứng, chỉ chuẩn bị Git trong
   phạm vi được duyệt. Không tự commit/push/PR, gửi tin nhóm hoặc sửa repo sách.

Không dùng subagent trong task này theo lựa chọn trước của người dùng.
