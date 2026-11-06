# Thiết kế REST API — Phiên bản code 3.0

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

**Ownership:** Payment Service queries only transactions with `payer_id` in the caller JWT. Gateway explicitly forwards this route to Payment Service, not User Service. Error `401` invalid JWT; `422` bad pagination. Trang bắt đầu từ 1; size từ 1 đến 50.

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

Body bắt buộc: `{ "acceptTerms": true }`. Backend từ chối nếu chưa đồng ý điều khoản. Phản hồi `200`: `{ "transactionId":"<uuid>", "status":"OTP_SENT", "otpExpiresAt":"2026-11-06T03:05:00Z", "otpExpiresIn":299 }`. OTP không xuất hiện trong response. Hòm thư local: http://localhost:8025. OTP dài 6 số, hiệu lực tối đa 5 phút và không vượt thời hạn giữ chỗ, không trùng với OTP đang còn hiệu lực của giao dịch khác. Lỗi: `401`, `403`, `404`, `409` sai trạng thái, `410` giữ chỗ hết hạn, `503` SMTP lỗi.

### POST `/api/v1/payments/{transactionId}/verify-otp` — Xác thực và thanh toán

Request: `{ "otp":"012345" }`.

Response `200`: transaction object với `status` `SUCCESS`, `PROCESSING` (đang retry, không thanh toán lại) hoặc `REVIEW` (cần đối soát thủ công). Lỗi: `401`, `403`, `404`, `409` trạng thái không hợp lệ, `410` hết hạn, `422` OTP sai/số dư không đủ, `429` quá 5 lần OTP.

`SUCCESS` chỉ được ghi sau khi **User Service đã debit idempotently** và **Tuition Service xác nhận `PAID` cho chính transaction**. Timeout không được tự động hiểu là chưa trừ tiền. Worker tiếp tục đối soát transaction `PROCESSING`.

### POST `/api/v1/payments/{transactionId}/cancel` — Hủy trước khi xử lý

Response `200`: payment object đầy đủ, `status` là `CANCELLED`. Cho phép từ `INIT`, `PENDING_CONFIRM` và `OTP_SENT`; gọi lại CANCELLED trả kết quả cũ; sau khi đã bắt đầu xử lý không được hủy. Lỗi: `401`, `403`, `404`, `409`.

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
| Tuition | POST | `/internal/tuitions/{tuition_id}/reserve` | Body `{transaction_id,expires_at}`, giữ chỗ theo hạn UTC cố định của payment |
| Tuition | POST | `/internal/tuitions/{tuition_id}/start` | Giữ chỗ → PROCESSING, không tự hết hạn |
| Tuition | POST | `/internal/tuitions/{tuition_id}/mark-paid` | Chỉ transaction đang xử lý mới có quyền ghi PAID |
| Tuition | POST | `/internal/tuitions/{tuition_id}/release` | Chỉ chính transaction giữ chỗ được release, không mở lại PAID |

## E. Status và validation

- `UNPAID` → `PENDING_PAYMENT` → `PROCESSING` → `PAID` (Tuition). Hủy/OTP lỗi trước debit có thể quay lại `UNPAID`.
- `INIT` → `PENDING_CONFIRM` → `OTP_SENT` → `PROCESSING` → `SUCCESS` (Transaction). Nhánh `FAILED`, `EXPIRED`, `CANCELLED`; `REVIEW` nếu có mâu thuẫn cần giải quyết thủ công.
- Invalid JWT: `401`, wrong ownership `403`, no record `404`, duplicate/invalid transition `409`, expired OTP `410`, insufficient balance `422`, 5 wrong OTP `429`, upstream/SMTP failure `503`.
- Pydantic structural errors use default FastAPI `422` schema; business errors use standard JSON `{"detail":"..."}`. IDs are UUID and amounts are integer VND.
- Basic idempotency: header for POST create + unique ledger by transaction ID. Multiple calls to verify-OTP after `SUCCESS` return the existing result, not a second debit.

## F. Các trường bổ sung của payment v3

Mọi payment object (create, verify, cancel, GET, từng item history) còn có:

```json
{
  "tuitionFeeId": "c0eebc99-9c0b-4ef8-bb6d-6bb9bd380c33",
  "expiresAt": "2026-11-06T03:08:00Z",
  "otpExpiresAt": null,
  "releasePending": true
}
```

otpExpiresAt có giá trị sau khi cấp OTP. expiresAt cố định từ lúc tạo. releasePending chỉ báo tác vụ cần cleanup; SUCCESS luôn false, kết quả FAILED/EXPIRED/CANCELLED có thể tạm true khi service đang gián đoạn. Tiền là integer VND, timestamps ISO 8601 có múi giờ.

POST create trả 201 khi PENDING_CONFIRM, 200 khi trả lại trạng thái khác còn hợp lệ; FAILED 409, EXPIRED 410. Retry cùng key và cùng fee giữ cùng transaction ID; đổi fee trả 409. Hãy giữ cùng key nếu không biết request trước đã tạo được hay chưa. INIT gặp lỗi mạng trả 503 nhưng có thể được worker khôi phục; không tạo yêu cầu mới trước khi xem lịch sử.

Mọi endpoint public có bảo vệ danh tính trả 401 nếu JWT sai/hết hạn. Truy cập payment của người khác trả 403. Các request sai UUID/OTP/model nhận 422. Khóa giao dịch quá bận có thể trả 503 để client tải lại trạng thái.

## G. Response và lỗi API nội bộ

Các lỗi chung: 403 thiếu/sai service token; 422 cấu trúc/UUID sai; 404 tài nguyên không tồn tại. Thành công đều là HTTP 200.

| Endpoint | Response 200 | Lỗi nghiệp vụ bổ sung |
|---|---|---|
| GET user account | `{payerId,fullName,email,accountId,balance}` | 404 không có tài khoản mặc định |
| POST debit | `{status:"DEBITED" hoặc "ALREADY_DEBITED",newBalance}` | 403 sai chủ; 409 ID đã gắn dữ liệu khác; 422 thiếu tiền |
| GET debit | `{debited:true/false,amount:integer/null}` | Không có debit trả false, không phải 404 |
| GET tuition fee | `{id,mssv,studentName,semester,amount,status,reservedBy,reservedUntil,paidBy}` | 404 không có khoản học phí |
| POST reserve | `{status,reservedBy,expiresAt}`; replay giữ chỗ có thể chỉ `{status,reservedBy}` | 409 đã PAID/giữ bởi người khác/hết hạn; 422 deadline không hợp lệ |
| POST start | `{status:"PROCESSING" hoặc "PAID"}` | 409 sai chủ/hết hạn/sai trạng thái |
| POST mark-paid | `{status:"PAID",transactionId}` | 409 sai chủ/sai trạng thái |
| POST release | `{status:"UNPAID" hoặc "NO_CHANGE"}` | 409 nếu chính giao dịch đã PAID; giao dịch khác đang sở hữu trả NO_CHANGE |

Body start/mark-paid/release: `{ "transaction_id":"<uuid>" }`; reserve thêm `"expires_at":"<ISO 8601 timezone-aware>"`; debit thêm payer_id và amount nguyên dương. Backend Payment chuyển HTTP 5xx của upstream thành 503 để retry an toàn.

## H. Tài liệu tương tác

Gateway: `/docs/user`, `/docs/tuition`, `/docs/payment`; OpenAPI JSON tương ứng `/openapi/user.json`, `/openapi/tuition.json`, `/openapi/payment.json`. Bản JSON xuất từ mã nằm tại `docs/openapi/`. Các endpoint health không yêu cầu JWT; trả `{ "status":"ok" }` khi truy vấn database được.
