# iBanking TDTU — Phân hệ đóng học phí (Microservices Demo)

**Dự án môn CS504070 – Kiến trúc hướng dịch vụ, HK1/2026–2027.** Đây là **mô phỏng phục vụ học tập**, KHÔNG phải ứng dụng ngân hàng thật, không dùng tiền thật.

## 1. Thành phần

- **Frontend + Nginx API Gateway:** http://localhost:8080 — giao diện và một đầu mối REST duy nhất.
- **User/Account Service:** xác thực username/password qua JWT, thông tin người nộp, số dư, debit ledger idempotent.
- **Tuition Service:** tra cứu MSSV, khoản học phí, giữ chỗ theo transaction, đánh dấu `PAID` có điều kiện.
- **Payment Service:** tạo giao dịch, email OTP 5 phút, xác thực 1 lần, quản lý Saga, lịch sử, kiểm tra/khôi phục giao dịch dở dang.
- **PostgreSQL:** 3 database độc lập (`user_db`, `tuition_db`, `payment_db`) với foreign keys **chỉ trong mỗi database**.
- **Mailpit:** http://localhost:8025 — hộp thư thử nghiệm nhận OTP và email xác nhận thành công (không gửi email ra Internet).

Tài liệu: `docs/REST_API_Design.md`, `docs/DESIGN_DECISIONS.md`, `docs/DEMO_SCRIPT.md`, `docs/RUBRIC_CHECKLIST.md`. Các sơ đồ `.svg`, `.png`, `.dot` (có thể sửa bằng Graphviz) ở `docs/diagrams/`.

## 2. Khởi chạy Windows / macOS / Linux

Yêu cầu: Docker Desktop (hoặc Docker Engine và Docker Compose plugin). Chạy các lệnh sau tại **thư mục chứa file `docker-compose.yml`**:

```bash
docker compose up -d --build
docker compose ps
```

Mở http://localhost:8080 để dùng hệ thống, http://localhost:8025 để xem OTP demo.

**Thông tin đăng nhập demo** (`password` đều là `123456`):

| Username | Tài khoản | Số dư gốc | Email demo |
|---|---|---:|---|
| `nguyenvana` | A | 15.000.000 đ | user.a@demo.local |
| `tranthib` | B | 20.000.000 đ | user.b@demo.local |
| `phaminhc` | C | 2.000.000 đ | user.c@demo.local |
| `doquangd` | D | 20.000.000 đ | user.d@demo.local |

Các MSSV demo: `523H0120` (11.500.000 đ, chưa thanh toán); `523H0121` (9.800.000 đ, chưa thanh toán); `523H0122` (7.400.000 đ, đã thanh toán); `523H0123` (8.700.000 đ, chưa thanh toán).

Lưu ý: dữ liệu thay đổi sau mỗi lần thanh toán và tồn tại trong Docker volume. **Chỉ khi cần làm mới dữ liệu demo**, sau khi đã sao lưu nếu cần, chạy:

```bash
docker compose down -v  # XÓA TOÀN BỘ DỮ LIỆU LƯU TRONG VOLUME DEMO
docker compose up -d --build
```

Đóng hệ thống mà không xóa dữ liệu: `docker compose down`. Xem lỗi: `docker compose logs -f payment-service` (hoặc `user-service`, `tuition-service`, `postgres-db`).

## 3. Luồng demo trên giao diện

1. Đăng nhập bằng `nguyenvana` / `123456`. Giao diện tự hiển thị tên, email, số dư người nộp (không cho sửa).
2. Nhập MSSV `523H0120`, tra cứu học phí (được phép đóng cho sinh viên khác).
3. Chọn **Thanh toán**, đọc tên SV, số tiền, người nộp và tích đồng ý điều khoản.
4. Nhấn **Gửi OTP qua email**. Mở Mailpit tại http://localhost:8025, đọc email gửi tới `user.a@demo.local`.
5. Quay lại giao diện, nhập 6 chữ số từ email, xác nhận (OTP chỉ còn hiệu lực tối đa 5 phút, mỗi OTP dùng một lần và thử sai tối đa 5 lần).
6. Xem số dư đã trừ 11.500.000 đ còn 3.500.000 đ, trạng thái học phí `PAID`, lịch sử giao dịch `SUCCESS`, email xác nhận trong Mailpit.
7. Thử tra cứu lại MSSV đã đóng: không thể thanh toán lần thứ hai. Thử user C (2.000.000 đ) và khoản học phí chưa đóng 9.800.000 đ: hệ thống trả lỗi không đủ số dư.

## 4. Automated tests

**Kiểm tra tĩnh, không cần Docker:**

```bash
python -m unittest discover -s tests -p 'test_static_contracts.py' -v
```

**Kiểm tra concurrency tích hợp** (cần Docker chạy, database phải mới reset, sử dụng dữ liệu seed):

```bash
python -m pip install -r tests/requirements.txt
python tests/test_concurrency.py
```

Hai case có assertion:
1. Cùng tài khoản A, đồng thời xác thực thanh toán hai học phí 11.500.000 và 9.800.000 đ trên số dư 15.000.000: chỉ một lần debit thành công, số dư không âm.
2. Tài khoản B và D cùng khởi tạo thanh toán MSSV `523H0123`: chỉ một bên giữ chỗ/thanh toán thành công, bên còn lại bị `409`, chỉ một tài khoản bị trừ tiền.

**Quan trọng:** bộ integration test tiêu thụ và thay đổi dữ liệu demo. Chạy integration test trước khi demo thủ công hoặc reset volume **chỉ khi bạn chấp nhận xóa dữ liệu demo**. Kết quả test chỉ được xem là đã xác minh khi bạn chạy thành công trên máy của bạn.

## 5. Điểm kiểm soát transaction

- Frontend không được tự quyết định `payer_id`, `account_id` hay `amount`: backend lấy danh tính từ JWT và học phí từ Tuition Service.
- Service nội bộ chỉ mở trong Docker network, yêu cầu header `X-Service-Token` và không được expose qua gateway.
- Tiền dùng `NUMERIC(18,0)` và Python integer để biểu diễn VND; số dư không được âm bởi constraint và khóa dòng `SELECT ... FOR UPDATE`.
- `account_debits.transaction_id` là PK, đảm bảo cùng một giao dịch chỉ ghi nợ một lần; các lần retry không trừ lặp.
- Mỗi khoản học phí có một transaction giữ chỗ. Khi bắt đầu xử lý, trạng thái sang `PROCESSING` và không tự giải phóng; worker retry để không mất ghi nhận sau khi đã trừ tiền.
- Nếu trừ tiền thành công nhưng cập nhật học phí tạm thất bại: trả `PROCESSING`, worker thử lại; không khẳng định giao dịch thất bại và không cho thanh toán lại. Lỗi bất thường chuyển `REVIEW` để đối soát thủ công.
- OTP không hiển thị qua API, lưu HMAC keyed và kiểm tra thời hạn, tối đa 5 lần thử, cờ used; mã đang hiệu lực không trùng nhau.

## 6. Giới hạn ngoài phạm vi bài giữa kỳ

- Không có cổng ngân hàng thật, sổ cái kép (double-entry ledger), hệ thống đối soát tự động độc lập, chống gian lận hoặc tuân thủ pháp lý ngân hàng.
- Worker retry trong chính Payment Service chỉ phù hợp demo; production nên dùng durable queue, worker riêng, checkpoint, monitoring/alerting và quy trình xử lý `REVIEW`.
- PostgreSQL dùng chung một server/credential quản trị cho ba DB để đơn giản hóa demo; production dùng tài khoản DB per-service với quyền tối thiểu, secret manager, TLS/mTLS, rate limiting, CSRF/XSS protections và kiểm thử bảo mật.
- Frontend demo dùng `sessionStorage` JWT; production cần cân nhắc session cookie HttpOnly/SameSite, refresh-token và chính sách CSP/XSS.
- Không công khai gateway/mailpit lên Internet khi còn sử dụng secrets mặc định trong Compose; chỉ bind `127.0.0.1` để demo cục bộ.

## 7. Cấu trúc chính

```text
ibanking-tuition-system/
  docker-compose.yml       # Postgres, 3 services, Nginx Gateway, Mailpit
  .env.example              # Tùy chọn cấu hình secrets qua .env riêng
  frontend/index.html      # UI toàn bộ workflow + lịch sử
  gateway/default.conf     # /api/v1 routing; chặn /internal
  database/init/           # 3 schemas + seed demo
  services/user-service/   # auth + debit ledger
  services/tuition-service/# fee reservation + paid
  services/payment-service/# OTP + saga + retry + history
  tests/                   # contract tests, two concurrency integration cases
  docs/                    # REST, UML, design, demo, checklist
```


## 8. Lưu ý bản sửa UUID
Nếu đã chạy bản ZIP trước và test concurrency bị `FAILED`/`422`, dùng bản hotfix này và rebuild: `docker compose up -d --build`. Dữ liệu cũ không tự xóa. Để làm sạch riêng môi trường DEMO từ đầu, cần sao lưu nếu có dữ liệu cần giữ, sau đó chạy `docker compose down -v` (xóa volume PostgreSQL) rồi `docker compose up -d --build`. Sau khi reset, dùng giao diện thanh toán trước; bộ test concurrency sẽ thay đổi số dư và học phí.
