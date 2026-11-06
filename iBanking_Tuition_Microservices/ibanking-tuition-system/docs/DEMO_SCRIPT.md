# Kịch bản demo/bảo vệ 8–12 phút

1. **Kiến trúc (1–2 phút):** mở `diagrams/microservices.svg`; 3 service độc lập có database riêng. Nginx API Gateway định tuyến công khai; Payment là Saga Orchestrator, giao tiếp nội bộ qua HTTP có service token.
2. **Use Case (1 phút):** mở `diagrams/usecase.svg`; user login, xem số dư, MSSV tra cứu, thanh toán cả khoản, OTP email, lịch sử. Có thể đóng cho người khác, không có đăng ký.
3. **ERD (1 phút):** mở `diagrams/erd.svg`; giải thích USER–ACCOUNT 1:N, STUDENT–TUITION 1:N, ACCOUNT_DEBIT có `transaction_id` unique, OTPS 1:0..1, Payment lưu logical references across DB.
4. **Workflow (3 phút):** login `nguyenvana/123456`, MSSV `523H0120`, xem 11.5 triệu và số dư 15 triệu, xác nhận, mở Mailpit `localhost:8025`, đọc OTP (không hiển thị trong API), nhập OTP, hiển thị SUCCESS, balance 3.5 triệu, tuition PAID và email receipt.
5. **Chống double payment (1 phút):** tra lại chính MSSV và thấy nút thanh toán biến mất. Phân tích reservation theo transaction, row-level lock, kiểm soát chủ giữ chỗ.
6. **Chống double spend (1 phút):** mô tả test `tests/test_concurrency.py`: 1 tài khoản 15 triệu đồng thời trả 11.5 triệu và 9.8 triệu, chỉ một thành công; lấy dòng `account_debits.transaction_id` làm khóa idempotency.
7. **Lỗi mạng (1 phút):** mô tả quá trình UPDATE `PROCESSING` trước khi debit, retry khi tuition-service lỗi; không cho phép giao dịch được khai báo FAILED sai sau khi đã trừ tiền. REVIEW cần xử lý thủ công.
8. **REST + triển khai (1 phút):** mở `docs/REST_API_Design.md`, các HTTP status code, `docker-compose.yml` và `README.md`.

**Demo cần hệ thống chạy và DB seed mới**. Hai bài test concurrency sẽ làm thay đổi số dư học phí demo; chỉ chạy trước demo hoặc reset dữ liệu một cách có chủ ý.

## Câu hỏi dự phòng

- **Sao không gộp 1 DB?** Mỗi service sở hữu dữ liệu riêng và không cập nhật DB của service khác. Nhược điểm: distributed transaction cần saga.
- **Nếu debit thành công, update tuition thất bại?** Chuyển PROCESSING và retry idempotent, không trừ lần nữa. Trường hợp bất thường REVIEW cần đối soát.
- **Làm sao bảo đảm hai người không cùng thanh toán?** Reservation exclusive tại tuition row, tx ID khớp mới được mark-paid; PAID không cho revert.
- **OTP an toàn thế nào?** CSPRNG, mã duy nhất đang hoạt động, HMAC, 5 phút, 1 lần, 5 lần nhập sai; gửi qua SMTP tới email người nộp.
- **Những hạn chế?** Chưa đủ production: durable queue, reconciliation operator tools, real banking rails, ledger kép, HTTPS/mTLS, RBAC, centralized observability.
