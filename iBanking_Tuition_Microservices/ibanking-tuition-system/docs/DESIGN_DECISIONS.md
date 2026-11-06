# Thiết kế kiến trúc, transaction và concurrency

## 1. Ranh giới dịch vụ

User/Account sở hữu users/accounts/account_debits; Tuition sở hữu students/tuition_fees; Payment sở hữu transactions/otps. Mỗi service có tiến trình, API và database riêng; chỉ truy cập database của mình. Gateway là điểm vào của web. SMTP phục vụ OTP và xác nhận.

Đây là cách áp dụng phân tách trách nhiệm và giao tiếp theo hợp đồng REST cho bài SOA/microservices. Ba database nằm trên cùng PostgreSQL server để chạy demo thuận tiện; không có transaction SQL chung bao phủ cả ba service.

## 2. Giao tiếp và lỗi

HTTP nội bộ có X-Service-Token và timeout 5 giây. Gateway không định tuyến endpoint thay đổi tiền/học phí nội bộ. Các service không công bố cổng riêng ra máy host. Lỗi mạng và HTTP 5xx được coi là chưa rõ kết quả và sẽ thử lại. Những lỗi nghiệp vụ rõ ràng như thiếu số dư được kết thúc và cleanup. Không dùng Kafka, Kubernetes hay dịch vụ ngoài không cần thiết cho đề.

## 3. Transaction cục bộ và hai tình huống bắt buộc

**Cùng tài khoản:** User Service SELECT FOR UPDATE tài khoản; kiểm tra sở hữu; chèn ledger theo transaction ID duy nhất; UPDATE có điều kiện balance >= amount. Chèn ledger và trừ tiền nằm trong cùng transaction SQL. Thiếu tiền làm rollback cả hai. Một lần gửi lại đã ghi nợ chỉ trả ALREADY_DEBITED khi account, payer và amount đều khớp. CHECK balance >= 0 là ràng buộc bổ sung.

**Cùng học phí:** Tuition Service khóa dòng khoản học phí và chỉ cho một transaction giữ chỗ còn hiệu lực. start và mark-paid đòi đúng chủ giữ chỗ; PAID chỉ được ghi cho transaction đã PROCESSING và không mở lại sau thanh toán. Payment DB có unique index chỉ cho một SUCCESS mỗi tuition_fee_id.

**Cùng một giao dịch:** PostgreSQL advisory transaction lock theo mã payment tuần tự hóa confirm/verify/cancel/recovery trên nhiều tiến trình. Các thay đổi vẫn commit bền bằng những transaction SQL ngắn; advisory lock chỉ điều phối. Tiến trình chết thì khóa tự được PostgreSQL giải phóng. Worker dùng try-lock để bỏ qua giao dịch đang có luồng khác xử lý. Kết quả terminal được bảo vệ trước cập nhật cũ.

async/await giúp xử lý chờ I/O; tính đúng đắn của số dư ở đây do transaction, khóa và ràng buộc database. Không dựa vào biến Python hoặc khóa trong một tiến trình để chống chi vượt số dư.

## 4. Điều phối Saga và phục hồi

Luồng: INIT → PENDING_CONFIRM → OTP_SENT → PROCESSING → SUCCESS. Các kết quả khác: FAILED, EXPIRED, CANCELLED; REVIEW dành cho vi phạm bất biến cần kiểm tra. completed_at được điền cho trạng thái kết thúc.

1. INIT và hạn giữ chỗ được lưu trước khi gọi reserve. Mất phản hồi hoặc dừng tiến trình: worker tiếp tục reserve bằng cùng ID, hoặc đóng EXPIRED nếu quá hạn.
2. Chấp nhận OTP và đổi PROCESSING cùng một SQL transaction trước khi gửi debit.
3. start khóa học phí ở PROCESSING, không tự hết hạn; retry debit theo cùng ID, rồi mark-paid theo cùng ID.
4. Chỉ sau cả hai API hoàn tất mới lưu SUCCESS. Nếu dừng sau debit hoặc sau PAID, worker thử lại các bước idempotent và hoàn tất.
5. Khi FAILED/EXPIRED/CANCELLED, release_pending vẫn TRUE đến lúc API release xác nhận. Worker tiếp tục cleanup dù payment đã kết thúc. Trước khi mở học phí luôn tra ledger: có ghi nợ thì chuyển REVIEW, giữ học phí để kiểm tra.
6. SUCCESS chưa gửi được email được worker thử lại. Lỗi email không thay đổi giao dịch tài chính đã hoàn tất.

Worker quét định kỳ 3 giây, retry_after tránh vòng lặp liên tục; xử lý INIT, PROCESSING, giao dịch chờ đã quá hạn, cleanup tồn đọng và biên nhận chưa gửi. Trạng thái nằm trong PostgreSQL nên khởi động lại Payment Service vẫn phục hồi được.

## 5. OTP, xác thực và dữ liệu đầu vào

JWT ký HS256, yêu cầu sub/exp, thời hạn phiên 1 giờ. Mật khẩu PBKDF2-HMAC-SHA256, salt riêng, 600.000 vòng; dữ liệu demo dùng 123456. Backend lấy payer từ JWT và amount từ Tuition, Pydantic cấm trường dư trong request payment/OTP/confirm.

Mã ngẫu nhiên 6 chữ số được sinh bằng secrets; HMAC gắn transaction để xác minh; fingerprint HMAC toàn cục cùng unique constraint và advisory lock cấp mã ngăn trùng mã đang hiệu lực. Không trả mã qua API hoặc lưu plaintext trong database. Một giao dịch chỉ chấp nhận thành công một lần; gửi lại sau SUCCESS trả kết quả đã có, không thực hiện thanh toán lại.

Hạn OTP = min(thời điểm cấp + 5 phút, hạn giữ chỗ). API trả otpExpiresAt thực tế. Sai 5 lần thì FAILED. SMTP lỗi thu hồi mã đã phát và trả về PENDING_CONFIRM; người dùng thử gửi lại. Khi hủy/hết hạn, mã không còn sử dụng được.

## 6. REVIEW và đối soát

REVIEW không phải thành công. Giao diện hiển thị cần đối soát và không tự cho thanh toán lại. Khi kiểm tra môi trường demo, truy vấn theo cùng transaction_id trong transactions, account_debits và reserved_by/paid_by_transaction_id của tuition_fees. Giữ nguyên dữ liệu để xác định bước đã commit; chỉ mở học phí khi chắc chắn chưa ghi nợ. Không tự hoàn tiền hoặc đổi PAID về UNPAID. Luồng quản trị ngân hàng thật không nằm trong đề.

SMTP không cung cấp bảo đảm đúng một lần qua mọi sự cố: tiến trình có thể dừng sau khi thư được nhận nhưng trước khi lưu notification_sent_at. Message-ID biên nhận cố định nhận diện cùng giao dịch. Trường hợp gửi lặp email không gây ghi nợ hay thanh toán lặp.

## 7. Liên hệ tài liệu đã học

| Nội dung được cung cấp | Áp dụng |
|---|---|
| Python Type Hint, Pydantic | Model request/response, kiểm tra UUID, OTP, số tiền và dữ liệu bắt buộc |
| Concurrency async/await | Phân biệt xử lý đồng thời I/O với bảo vệ dữ liệu dùng chung; lifespan quản lý worker |
| FastAPI, path/query/body/header | Các endpoint REST, phân trang, JWT/header nội bộ, JSON request, OpenAPI/Swagger |
| Data types UUID/datetime, nested/list models | ID giao dịch, thời hạn UTC, danh sách kết quả có kiểu |
| Tên chương thiết kế/giao tiếp/quản lý dữ liệu/bảo mật/tích hợp/triển khai | Phân tách service, REST nội bộ, PostgreSQL, OTP/email và Docker Compose |

Row lock, idempotency, advisory lock và Saga retry là kỹ thuật bổ sung cần thiết cho yêu cầu tài chính/đồng thời của đề; đã được người dùng đồng ý bổ sung. Không khẳng định các nội dung chi tiết này đều có trong slide chưa được cung cấp.
