# Checklist rubric CS504070 (đối chiếu bài và các bài test)

| # | Rubric / điểm tối đa | Đã được bổ sung trong source | Minh chứng cần demo/xác nhận |
|---|---|---|---|
| 1 | Analysis/UML (1.0) | Use Case + ERD SVG/DOT, phân tích nghiệp vụ | Mở sơ đồ, giải thích các quan hệ và external actor |
| 2 | Microservices (1.5) | 3 FastAPI service, 3 database, Nginx Gateway, SMTP Mailpit, Architecture SVG | `docker compose ps`, diagram, theo dõi API / DB ownership |
| 3 | REST API (1.0) | `REST_API_Design.md` đồng bộ code, request/response, status | Login, GET tuition, create/confirm/verify, GET history, lỗi 401/409/422 |
| 4 | Persistence (1.0) | SQL init 3 DB, FK nội bộ, constraints, sample seed | `docker compose logs postgres-db`, kiểm tra các bảng và balances |
| 5 | Workflow (2.0) | User login, payer read-only, MSSV, all-or-nothing, OTP SMTP, receipt, history | Full success demo + email Mailpit, OTP fail/expire/cancel |
| 6 | Concurrency (1.5) | `FOR UPDATE`, immutable PAID, exclusive reservation, unique account_debits, saga/recovery | Chạy `python tests/test_concurrency.py` trên **DB mới**; chụp PASS cases |
| 7 | UI (0.5) | Frontend login → search → confirm + terms → OTP → history | Mở `localhost:8080`, đi qua toàn bộ luồng |
| 8 | Docs/deploy/demo (1.5) | README, diagrams, scripts, Docker Compose, test | Thầy clone, chạy Docker, demo lại được |

**Lưu ý:** đây là checklist những phần đã *được lập trình/tạo tài liệu*, không đồng nghĩa với chứng nhận đã test E2E. Tại môi trường sửa code không có Docker/PostgreSQL đang chạy, cần chạy bài test tích hợp trên máy có Docker. Kết quả điểm do giảng viên đánh giá.

## Ưu tiên trước ngày nộp

1. Chạy `docker compose up -d --build` và kiểm tra http://localhost:8080 + http://localhost:8025.
2. Chạy `python -m unittest discover -s tests -p test_static_contracts.py -v`.
3. Chạy integration test với **database seed mới**, xem hai PASS và các assertion; sửa lỗi môi trường nếu có.
4. Mở SVG của 3 biểu đồ, đối chiếu với source và đề.
5. Demo lại luồng end-to-end và quay video/chụp màn hình làm bằng chứng.
6. Nộp ZIP source (không commit `.env`, không gửi khóa thật, không nộp volume DB).
