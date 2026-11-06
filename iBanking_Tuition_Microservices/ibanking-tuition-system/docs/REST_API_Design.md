# Thiết kế REST API — Phiên bản code 2.0

Tất cả endpoint public đi qua **Nginx API Gateway** tại `http://localhost:8080` với prefix `/api/v1`. Các endpoint trừ login phải gửi `Authorization: Bearer <accessToken>` (JWT được cấp từ login). Mỗi request và response JSON dùng đơn vị **VND nguyên** (không có phần thập phân).

## A. Authentication & User

### POST `/api/v1/auth/login`

Request: `{ "username": "nguyenvana", "password": "123456" }`

Response `200`: `{ "accessToken": "<JWT>", "expiresIn": 3600, "user": { "id":"<uuid>", "username":"nguyenvana", "fullName":"Nguyễn Văn A", "email":"user.a@demo.local", "phone":"0901234567", "account": { "id":"<uuid>", "accountNumber":"1020304050", "balance":15000000 } } }`

Lỗi: `401` sai username/password; `422` dữ liệu không hợp lệ.

### GET `/api/v1/users/me`

Response `200`: `{ "id":"<uuid>", "username":"nguyenvana", "fullName":"Nguyễn Văn A", "phone":"0901234567", "email":"user.a@demo.local", "balance":15000000, "account": { "id":"<uuid>", "accountNumber":"1020304050" } }`.

Lỗi: `401` JWT không hợp lệ/hết hạn; `404` không tồn tại.

### GET `/api/v1/users/me/transactions?page=1&size=10`

Response `200`: `{ "page":1, "size":10, "total":1, "items":[ { "transactionId":"<uuid>", "payerId":"<uuid>", "mssv":"523H0120", "studentName":"Lê Văn C", "semester":"HK1/2026-2027", "amount":11500000, "status":"SUCCESS", "failureReason":null, "createdAt":"2026-11-01T13:00:00Z", "completedAt":"2026-11-01T13:00:10Z" } ] }`.

**Ownership:** Payment Service queries only transactions with `payer_id` in the caller JWT. Gateway explicitly forwards this route to Payment Service, not User Service. Error `401` invalid JWT; `422` bad pagination.

## B. Tuition

### GET `/api/v1/tuitions/{mssv}`

Response `200`:

```json
{
  "student": {"student_code":"523H0120", "full_name":"Lê Văn C", "faculty":"Công nghệ Thông tin", "major":"Kỹ thuật Phần mềm"},
  "tuitionFees": [ {"id":"c0eebc99-9c0b-4ef8-bb6d-6bb9bd380c33", "semester":"HK1/2026-2027", "amount":11500000, "status":"UNPAID"} ]
}
```

Lỗi: `401`, `404` MSSV không có.

## C. Payment

### POST `/api/v1/payments` — Khởi tạo / giữ chỗ học phí

Headers: `Authorization: Bearer <token>` và `Idempotency-Key: <UUID ngẫu nhiên>` (8–100 ký tự).

Request: `{ "tuitionFeeId": "c0eebc99-9c0b-4ef8-bb6d-6bb9bd380c33" }`.

Response `201`: `{ "transactionId":"<uuid>", "payerId":"<uuid>", "mssv":"523H0120", "studentName":"Lê Văn C", "semester":"HK1/2026-2027", "amount":11500000, "status":"PENDING_CONFIRM", "failureReason":null, "createdAt":"2026-11-01T13:00:00Z", "completedAt":null }`.

**Server** tự lấy payer/account từ JWT và User Service; **amount** lấy từ Tuition Service. Không nhận số tiền từ client. Reservation tồn tại tối đa 8 phút trước khi bắt đầu xử lý. Trùng idempotency key của cùng user trả lại giao dịch hiện có (cùng tuition fee). Lỗi: `400` thiếu key, `401`, `404`, `409` học phí đã trả/đang bị giữ, `422` không đủ số dư lúc bắt đầu, `503` service lỗi.

### POST `/api/v1/payments/{transactionId}/confirm` — Đồng ý, gửi OTP email

Body không bắt buộc. Phản hồi `200`: `{ "transactionId":"<uuid>", "status":"OTP_SENT", "otpExpiresIn":300 }`. OTP không xuất hiện trong response. Hòm thư local: http://localhost:8025. OTP dài 6 số, hiệu lực 5 phút, không trùng với OTP đang còn hiệu lực của giao dịch khác. Lỗi: `401`, `403`, `404`, `409` sai trạng thái, `410` giữ chỗ hết hạn, `503` SMTP lỗi.

### POST `/api/v1/payments/{transactionId}/verify-otp` — Xác thực và thanh toán

Request: `{ "otp":"012345" }`.

Response `200`: transaction object với `status` `SUCCESS`, `PROCESSING` (đang retry, không thanh toán lại) hoặc `REVIEW` (cần đối soát thủ công). Lỗi: `401`, `403`, `404`, `409` trạng thái không hợp lệ, `410` hết hạn, `422` OTP sai/số dư không đủ, `429` quá 5 lần OTP.

`SUCCESS` chỉ được ghi sau khi **User Service đã debit idempotently** và **Tuition Service xác nhận `PAID` cho chính transaction**. Timeout không được tự động hiểu là chưa trừ tiền. Worker tiếp tục đối soát transaction `PROCESSING`.

### POST `/api/v1/payments/{transactionId}/cancel` — Hủy trước khi xử lý

Response `200`: `{ "transactionId":"<uuid>", "status":"CANCELLED" }`. Chỉ cho phép từ `PENDING_CONFIRM` và `OTP_SENT`; sau khi đã bắt đầu xử lý không được hủy. Lỗi: `401`, `403`, `404`, `409`.

### GET `/api/v1/payments/{transactionId}` — Xem giao dịch

Response `200`: giống object payment ở trên (status và failureReason có thể thay đổi). Chỉ chủ giao dịch xem được. Lỗi: `401`, `403`, `404`.

## D. Internal API (CHỈ giữa microservices)

Yêu cầu `X-Service-Token: <INTERNAL_API_TOKEN>`, không xuất bản qua gateway. **Không dùng trực tiếp từ trình duyệt**.

| Service | Method | Endpoint | Ý nghĩa |
|---|---|---|---|
| User | GET | `/internal/users/{payer_id}/account` | Xác minh chủ tài khoản, lấy account mặc định |
| User | POST | `/internal/accounts/{account_id}/debit` | Body `{transaction_id,payer_id,amount}`, row lock + ledger (idempotent) |
| User | GET | `/internal/accounts/{account_id}/debits/{transaction_id}` | Xác minh đã debit hay chưa khi recovery |
| Tuition | GET | `/internal/tuitions/{tuition_id}` | Lấy amount, trạng thái, transaction giữ chỗ |
| Tuition | POST | `/internal/tuitions/{tuition_id}/reserve` | Body `{transaction_id}`, giữ chỗ có thời hạn |
| Tuition | POST | `/internal/tuitions/{tuition_id}/start` | Giữ chỗ → PROCESSING, không tự hết hạn |
| Tuition | POST | `/internal/tuitions/{tuition_id}/mark-paid` | Chỉ transaction đang xử lý mới có quyền ghi PAID |
| Tuition | POST | `/internal/tuitions/{tuition_id}/release` | Chỉ chính transaction giữ chỗ được release, không mở lại PAID |

## E. Status và validation

- `UNPAID` → `PENDING_PAYMENT` → `PROCESSING` → `PAID` (Tuition). Hủy/OTP lỗi trước debit có thể quay lại `UNPAID`.
- `INIT` → `PENDING_CONFIRM` → `OTP_SENT` → `PROCESSING` → `SUCCESS` (Transaction). Nhánh `FAILED`, `EXPIRED`, `CANCELLED`; `REVIEW` nếu có mâu thuẫn cần giải quyết thủ công.
- Invalid JWT: `401`, wrong ownership `403`, no record `404`, duplicate/invalid transition `409`, expired OTP `410`, insufficient balance `422`, 5 wrong OTP `429`, upstream/SMPP failure `503`.
- Pydantic structural errors use default FastAPI `422` schema; business errors use standard JSON `{"detail":"..."}`. IDs are UUID and amounts are integer VND.
- Basic idempotency: header for POST create + unique ledger by transaction ID. Multiple calls to verify-OTP after `SUCCESS` return the existing result, not a second debit.
