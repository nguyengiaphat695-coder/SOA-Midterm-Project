# Những thay đổi so với file ZIP ban đầu

| Ban đầu | Bản đã sửa |
|---|---|
| Đăng nhập bằng email nhưng đề yêu cầu username; bcrypt seed không tương ứng 123456 | Có username, hỗ trợ email, PBKDF2 salted đúng mật khẩu demo; JWT 1 giờ |
| USER chứa balance trong biểu đồ nhưng code tách accounts | ERD mới gồm USER, ACCOUNT, ACCOUNT_DEBIT, STUDENT, TUITION_FEE, TRANSACTION, OTP |
| Payment nhận payer_id, account_id, amount tùy ý từ browser | Chỉ nhận tuitionFeeId, lấy payer JWT, account từ User Service, amount từ Tuition Service |
| OTP trả JSON và tự điền trên UI | OTP email qua Mailpit, HMAC lưu DB, 5 phút, một lần, 5 sai, không trùng mã còn hiệu lực |
| Public internal debit/mark-paid endpoints | Chỉ trong Docker private network và yêu cầu X-Service-Token |
| User debit tách biệt khiến mark-paid thất bại vẫn SUCCESS | `PROCESSING` retry-forward Saga; đánh dấu SUCCESS chỉ sau khi xác nhận cả debit và paid |
| Không có idempotency | Header Idempotency-Key và `account_debits.transaction_id` PK |
| Tuition lock không theo transaction, unlock có thể đổi PAID thành UNPAID | Giữ chỗ theo transaction_id, có expiration cho bước OTP, PAID bất biến |
| Thiếu transaction history và UI điều khoản | REST history + màn hình lịch sử, payer readonly, điều khoản trước email OTP |
| Lịch sử chỉ có Use Case, không code; REST docs khác endpoint | REST design cập nhật chính xác theo code đã sửa |
| Test concurrency sai tiền và chỉ có print, chưa đúng hai case của thầy | Hai integration tests có assertion trên PostgreSQL + Mailpit (cần Docker chạy để xác minh) |
| Chỉ có 3 service ports public, không có Gateway | Nginx API Gateway 8080 duy nhất; ports nội bộ không public |
| Chưa có Architecture Diagram / README | Ba SVG/PNG/DOT, README, design decisions, rubric checklist, demo script, Docker Compose |

## Chưa thể xác minh tại môi trường tạo bản sửa

Không có Docker/PostgreSQL/Mailpit runtime ở đây, vì vậy chưa thể khẳng định server khởi động hoặc chạy E2E pass. Đã kiểm tra cú pháp mã Python/JS, các hash mẫu, cú pháp cấu trúc Compose, HTML button wiring và dựng biểu đồ. Hãy thực hiện `docker compose up -d --build` và `python tests/test_concurrency.py` trên máy của bạn trước khi nộp.

Đây là hệ thống mô phỏng, chưa đủ điều kiện vận hành tài chính thực tế.


## 2026-11-01 — Hotfix UUID in payment/concurrency workflow
- PostgreSQL UUID values read via default psycopg2 can be plain strings, while FastAPI/Pydantic path and body UUID fields are `uuid.UUID`.
- Normalize transaction, payer, account and tuition UUID comparisons so `/start`, `/release`, debit ownership and retry checks work correctly.
- Previous version could respond FAILED/422 for both concurrency attempts and leave tuition PENDING_PAYMENT despite valid OTP.
- Rebuild Docker images and reset local demo volume **only if you accept losing all demo data** before re-running destructive integration tests.
