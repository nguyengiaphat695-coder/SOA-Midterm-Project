# iBanking TDTU — Phân hệ đóng học phí

Dự án giữa kỳ môn CS504070 — Kiến trúc hướng dịch vụ, HK1/2026–2027.
Ứng dụng mô phỏng: số dư, tài khoản và học phí là dữ liệu demo.

## 1. Chạy nhanh

Cần Docker Desktop đang chạy Linux containers và Docker Compose v2.
Giải nén, mở terminal tại thư mục chứa `docker-compose.yml`:

```sh
docker compose up -d --build --wait --wait-timeout 180
docker compose ps
```

- Giao diện: http://localhost:8080
- Email demo/Mailpit: http://localhost:8025
- Swagger: http://localhost:8080/docs/user, http://localhost:8080/docs/tuition, http://localhost:8080/docs/payment
- ReDoc: thay `/docs/` bằng `/redoc/`.

Nếu cổng đang được ứng dụng khác dùng, sao chép `.env.example` thành `.env`, đổi `WEB_PORT` và `MAIL_UI_PORT`, rồi chạy lại.

Bản này dùng Compose project `ibanking-tuition-v3`, tạo volume mới cho schema v3. Không dùng lại volume bản cũ. Thay đổi `.env` về mật khẩu PostgreSQL sau khi volume đã tạo không tự đổi mật khẩu trong database.

## 2. Tài khoản và dữ liệu mẫu

Mật khẩu tất cả tài khoản: **123456**.

| Username | Họ tên | Số dư ban đầu | Email |
|---|---|---:|---|
| nguyenvana | Nguyễn Văn A | 15.000.000 đ | user.a@demo.local |
| tranthib | Trần Thị B | 20.000.000 đ | user.b@demo.local |
| phaminhc | Phạm Minh C | 2.000.000 đ | user.c@demo.local |
| doquangd | Đỗ Quang D | 20.000.000 đ | user.d@demo.local |

| MSSV | Học phí | Trạng thái ban đầu |
|---|---:|---|
| 523H0120 | 11.500.000 đ | Chưa thanh toán |
| 523H0121 | 9.800.000 đ | Chưa thanh toán |
| 523H0122 | 7.400.000 đ | Đã thanh toán |
| 523H0123 | 8.700.000 đ | Chưa thanh toán |

## 3. Luồng sử dụng

1. Đăng nhập `nguyenvana / 123456`.
2. Tra cứu `523H0120`, chọn Thanh toán. Thông tin người nộp gồm tên, điện thoại, email lấy từ tài khoản và chỉ cho đọc.
3. Kiểm tra sinh viên, học phí, số dư; đồng ý điều khoản rồi gửi OTP.
4. Mở Mailpit, chọn email gửi đến `user.a@demo.local`, nhập mã sáu chữ số.
5. Kết quả thành công: số dư còn 3.500.000 đ, học phí đã thanh toán, lịch sử có SUCCESS và có email biên nhận.
6. Có thể tải lại trang rồi chọn Tiếp tục trong lịch sử để nhập OTP. Nút Chi tiết cho xem mã, thời gian, trạng thái và lý do kết thúc.

Giữ chỗ tối đa 8 phút từ khi tạo. OTP có hạn tối đa 5 phút và không vượt thời hạn giữ chỗ; giao diện hiển thị thời gian thực còn lại. OTP chỉ sử dụng thành công một lần. Năm lần nhập sai kết thúc giao dịch. Không thanh toán từng phần.

## 4. Kiến trúc và dữ liệu

| Thành phần | Trách nhiệm |
|---|---|
| Web + Nginx | Giao diện, định tuyến REST, Swagger/ReDoc |
| User/Account Service | Username/password, JWT, thông tin người nộp, số dư và ghi nợ chống lặp |
| Tuition Service | Sinh viên, khoản học phí, giữ chỗ độc quyền và trạng thái PAID |
| Payment Service | Giao dịch, điều khoản, OTP, điều phối thanh toán, lịch sử, khôi phục, email |
| PostgreSQL 15 | Ba database riêng; FK trong từng database, không ghi chéo database giữa service |
| Mailpit | Nhận thư SMTP demo; có thể cấu hình SMTP thật qua `.env` khi cần |

User/Account và Tuition dùng transaction PostgreSQL cục bộ và `SELECT ... FOR UPDATE`. Payment dùng Saga retry-forward: cùng transaction ID cho mọi lần thử, lưu trạng thái trước khi gọi dịch vụ và chỉ SUCCESS sau khi cả ghi nợ/học phí đã hoàn tất.

Một khóa advisory trong PostgreSQL bảo đảm một giao dịch chỉ có một luồng điều phối tại một thời điểm. Cờ `release_pending` lưu bền việc cần mở lại học phí; worker thử lại cả khi payment đã FAILED/EXPIRED/CANCELLED. Trước khi mở lại, kiểm tra sổ ghi nợ. Giao dịch đã ghi nợ không tự động hết hạn/mở học phí. Chi tiết tại `docs/DESIGN_DECISIONS.md`.

## 5. Kiểm thử

### Kiểm thử với PostgreSQL riêng, không thay đổi dữ liệu demo

```sh
docker compose --profile test run --build --rm test-runner
```

Runner thực hiện kiểm thử tĩnh, hành vi và 15 bài tích hợp trên các database `ibk_test_*` được tạo riêng rồi xóa. PostgreSQL là thật; các API FastAPI được gọi trong tiến trình kiểm thử, SMTP được thay bằng hộp thư trong bộ nhớ để kiểm tra nội dung. Không nhầm bộ này với kiểm thử toàn bộ Nginx/Mailpit.

### Kiểm thử toàn hệ thống HTTP + Mailpit

Sau khi stack chạy, trên Python 3.11+:

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r tests/requirements.txt
python tests/test_concurrency.py
```

Hai bài này dùng số dư/học phí seed của ứng dụng và thực sự thay đổi dữ liệu demo. Chạy trước demo thủ công hoặc dùng một Compose project/volume riêng. Cần dữ liệu seed ban đầu.

Chạy cục bộ những kiểm tra không cần database:

```sh
python -m pytest tests/test_static_contracts.py tests/test_payment_unit.py -v
node tests/test_frontend.cjs
```

Kết quả thực tế và phạm vi kiểm tra: `docs/TEST_RESULTS.md`. Các bài bị skip không được coi là đã đạt.

## 6. Dừng, xem lỗi và làm mới dữ liệu

```sh
docker compose logs --tail 100 payment-service
docker compose logs --tail 100 postgres-db
docker compose down
```

`down` giữ dữ liệu. Chỉ khi muốn xóa dữ liệu demo của project v3 để chạy lại từ seed:

```sh
docker compose down -v
docker compose up -d --build --wait --wait-timeout 180
```

Đang PROCESSING: đợi worker khôi phục, xem lại lịch sử. REVIEW: cần kiểm tra sổ ghi nợ, payment và chủ giữ chỗ; không sửa số dư/trạng thái tùy ý. Hướng dẫn đối soát ở `docs/DESIGN_DECISIONS.md`.

## 7. Tài liệu nộp và bảo vệ

- `docs/ANALYSIS.md`: nghiệp vụ, quy tắc dữ liệu, use case, luồng lỗi.
- `docs/diagrams/`: Use Case, ERD và Microservices Architecture, bản SVG/PNG/DOT.
- `docs/REST_API_Design.md`: URI, HTTP method, request, response và status code.
- `docs/openapi/`: hợp đồng OpenAPI xuất từ các service.
- `docs/DESIGN_DECISIONS.md`: transaction, concurrency, phục hồi, bảo mật, liên hệ bài học.
- `docs/RUBRIC_CHECKLIST.md`: đối chiếu đúng tám yêu cầu đề.
- `docs/DEMO_SCRIPT.md`: trình diễn và câu hỏi bảo vệ.
- `docs/TEST_RESULTS.md`: kết quả đã kiểm tra và giới hạn.
- `docs/CHANGELOG_FIXES.md`: thay đổi từ ZIP người dùng cung cấp.

Ứng dụng sử dụng tài khoản ngân hàng và tiền mô phỏng. Mailpit mặc định nhận email trong môi trường demo, không chuyển thư tới hộp thư Internet. SMTP có thể gửi lặp biên nhận nếu tiến trình dừng sau khi gửi mà chưa lưu xác nhận; Message-ID cố định giúp nhận diện cùng biên nhận, không phải bảo đảm SMTP gửi đúng một lần. Ngược lại, ghi nợ và thanh toán học phí được bảo vệ chống lặp bằng các ràng buộc và transaction tại database.
