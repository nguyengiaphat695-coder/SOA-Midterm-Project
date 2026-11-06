# Đối chiếu tám yêu cầu trong đề

Không tự gán trọng số hoặc bảo đảm điểm số; kết quả chấm do giảng viên quyết định.

| # | Yêu cầu | Nội dung bàn giao | Minh chứng nghiệm thu |
|---|---|---|---|
| 1 | Phân tích nghiệp vụ và dữ liệu, Use Case và ERD | ANALYSIS.md, diagrams/usecase.*, diagrams/erd.* | BR01–BR12, UC01–UC08, các bảng/quan hệ |
| 2 | Thiết kế microservices và giao tiếp | DESIGN_DECISIONS.md, diagrams/microservices.*, Compose | Ba service độc lập, ba database, HTTP, SMTP, gateway |
| 3 | Thiết kế REST API | REST_API_Design.md, openapi/*.json | URI, method, input/output, HTTP status và quyền truy cập |
| 4 | Hiện thực CSDL SQL/NoSQL | database/init/*.sql | Khởi tạo seed, PK/FK/unique/check |
| 5 | Hiện thực service và API | services/*/main.py | Đăng nhập, tra cứu, xác nhận, OTP, debit, PAID, history, email |
| 6 | Transaction và concurrency | FOR UPDATE, ledger, reservation, advisory lock, retry/cleanup | Hai case bắt buộc, mất phản hồi debit, lỗi release, OTP hết hạn |
| 7 | Giao diện web tích hợp API | frontend/, gateway/ | Thông tin người nộp chỉ đọc, nhập MSSV, điều khoản, OTP, kết quả, tiếp tục lịch sử |
| 8 | Tài liệu và trình diễn | README, DEMO_SCRIPT.md, TEST_RESULTS.md | Cài mới chạy được, demo lặp lại và giải thích cơ chế |

Trước khi nộp: chạy test-runner, chạy hai bài HTTP+Mailpit trên seed mới, demo thành công/OTP sai/thiếu tiền, kiểm tra mã nhóm/thành viên nếu báo cáo môn học yêu cầu, đóng gói source không kèm secrets thật hay volume database.
