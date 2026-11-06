"""Payment service: transaction-bound OTP, durable cleanup and retry-safe orchestration."""
import asyncio
import hashlib
import hmac
import logging
import os
import secrets
import smtplib
import threading
from contextlib import contextmanager, asynccontextmanager
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from uuid import UUID, uuid4

import httpx
import jwt
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

@asynccontextmanager
async def lifespan(_app):
    if not all((DB_URL, JWT_SECRET, INTERNAL_TOKEN, OTP_SECRET)):
        raise RuntimeError('Thiếu DATABASE_URL/JWT_SECRET/INTERNAL_API_TOKEN/OTP_SECRET')
    STOP.clear()
    worker = threading.Thread(target=reconciler_loop, daemon=True, name='payment-recovery')
    worker.start()
    try:
        yield
    finally:
        STOP.set()
        await asyncio.to_thread(worker.join, 6)


app = FastAPI(title='iBanking Payment Service', version='3.0.0', lifespan=lifespan,
              docs_url='/docs/payment', redoc_url='/redoc/payment', openapi_url='/openapi/payment.json')
log = logging.getLogger('payments')
DB_URL = os.getenv('DATABASE_URL', '')
USER_URL = os.getenv('USER_SERVICE_URL', 'http://user-service:3001')
TUITION_URL = os.getenv('TUITION_SERVICE_URL', 'http://tuition-service:3002')
JWT_SECRET = os.getenv('JWT_SECRET', '')
INTERNAL_TOKEN = os.getenv('INTERNAL_API_TOKEN', '')
OTP_SECRET = os.getenv('OTP_SECRET', '')
SMTP_HOST = os.getenv('SMTP_HOST', 'mailpit')
SMTP_PORT = int(os.getenv('SMTP_PORT', '1025'))
SMTP_FROM = os.getenv('SMTP_FROM', 'ibanking@demo.local')
SMTP_USERNAME = os.getenv('SMTP_USERNAME', '')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
SMTP_STARTTLS = os.getenv('SMTP_STARTTLS', 'false').lower() == 'true'
STOP = threading.Event()
TERMINAL = {'SUCCESS', 'FAILED', 'EXPIRED', 'CANCELLED', 'REVIEW'}


def now():
    return datetime.now(timezone.utc)


@contextmanager
def db():
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor, connect_timeout=5)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def transaction_lock(tx_id, blocking=True):
    """Serialize all handlers/workers for one payment, also across processes."""
    key = int.from_bytes(hashlib.sha256(str(tx_id).encode()).digest()[:8], 'big', signed=True)
    with db() as conn, conn.cursor() as cur:
        if blocking:
            cur.execute("SET LOCAL lock_timeout = '5s'")
            try:
                cur.execute('SELECT pg_advisory_xact_lock(%s)', (key,))
            except psycopg2.errors.LockNotAvailable:
                raise HTTPException(503, 'Giao dịch đang xử lý; vui lòng tải lại trạng thái')
            acquired = True
        else:
            cur.execute('SELECT pg_try_advisory_xact_lock(%s) AS acquired', (key,))
            acquired = cur.fetchone()['acquired']
        yield acquired


def require_user(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, 'Cần đăng nhập')
    try:
        claims = jwt.decode(authorization[7:], JWT_SECRET, algorithms=['HS256'], options={'require': ['exp', 'sub']})
        UUID(claims['sub'])
        return claims
    except (jwt.InvalidTokenError, KeyError, ValueError, TypeError):
        raise HTTPException(401, 'Phiên đăng nhập không hợp lệ hoặc đã hết hạn')


def upstream(method, base, path, **kwargs):
    try:
        with httpx.Client(timeout=5, headers={'X-Service-Token': INTERNAL_TOKEN}, trust_env=False) as client:
            response = client.request(method, base + path, **kwargs)
    except httpx.RequestError:
        raise HTTPException(503, 'Dịch vụ tạm gián đoạn; hệ thống sẽ khôi phục giao dịch')
    if response.status_code >= 400:
        try:
            detail = response.json().get('detail', 'Lỗi dịch vụ nội bộ')
        except ValueError:
            detail = 'Lỗi dịch vụ nội bộ'
        # Any server error is uncertain. A lost acknowledgement does not mean debit failed.
        raise HTTPException(503 if response.status_code >= 500 else response.status_code, detail)
    return response.json()


def send_email(to, subject, body, message_id):
    message = EmailMessage()
    message['From'], message['To'], message['Subject'] = SMTP_FROM, to, subject
    message['Message-ID'] = f'<{message_id}@ibanking.demo>'
    message.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=5) as smtp:
        if SMTP_STARTTLS:
            smtp.starttls()
        if SMTP_USERNAME:
            smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
        smtp.send_message(message)


def otp_hash(tx_id, code):
    return hmac.new(OTP_SECRET.encode(), f'{tx_id}:{code}'.encode(), hashlib.sha256).hexdigest()


class NewPayment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tuitionFeeId: UUID


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    acceptTerms: bool


class OTPBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    otp: str = Field(pattern=r'^[0-9]{6}$')


class PaymentView(BaseModel):
    transactionId: UUID
    payerId: UUID
    tuitionFeeId: UUID
    mssv: str
    studentName: str
    semester: str
    amount: int
    status: str
    failureReason: str | None
    createdAt: datetime
    completedAt: datetime | None
    expiresAt: datetime
    otpExpiresAt: datetime | None
    releasePending: bool


class OTPView(BaseModel):
    transactionId: UUID
    status: str
    otpExpiresAt: datetime
    otpExpiresIn: int


class HistoryView(BaseModel):
    page: int
    size: int
    total: int
    items: list[PaymentView]


def load_tx(tx_id, payer_id=None):
    with db() as conn, conn.cursor() as cur:
        cur.execute('SELECT * FROM transactions WHERE id=%s', (str(tx_id),))
        tx = cur.fetchone()
    if not tx:
        raise HTTPException(404, 'Không tìm thấy giao dịch')
    if payer_id and str(tx['payer_id']) != str(payer_id):
        raise HTTPException(403, 'Không phải chủ giao dịch')
    return tx


def public_tx(tx):
    return {'transactionId': str(tx['id']), 'payerId': str(tx['payer_id']), 'tuitionFeeId': str(tx['tuition_fee_id']),
            'mssv': tx['student_code'], 'studentName': tx['student_name'],
            'semester': tx['semester'], 'amount': int(tx['amount']), 'status': tx['status'],
            'failureReason': tx['failure_reason'], 'createdAt': tx['created_at'],
            'completedAt': tx['completed_at'], 'expiresAt': tx['expires_at'],
            'otpExpiresAt': tx['otp_expires_at'], 'releasePending': tx['release_pending']}


def set_status(tx_id, status, reason=None):
    """Caller holds transaction_lock. Terminal results cannot be overwritten by stale work."""
    with db() as conn, conn.cursor() as cur:
        cur.execute('''UPDATE transactions SET status=%s,failure_reason=%s,updated_at=now(),
          completed_at=CASE WHEN %s IN ('SUCCESS','FAILED','EXPIRED','CANCELLED','REVIEW')
                            THEN COALESCE(completed_at,now()) ELSE completed_at END,
          release_pending=CASE WHEN %s='SUCCESS' THEN FALSE ELSE release_pending END
          WHERE id=%s AND status NOT IN ('SUCCESS','FAILED','EXPIRED','CANCELLED','REVIEW')''',
                    (status, reason, status, status, str(tx_id)))
        if status in TERMINAL:
            cur.execute('UPDATE otps SET used=TRUE,code_fingerprint=NULL WHERE transaction_id=%s', (str(tx_id),))


def cleanup(tx):
    """Keep release_pending until acknowledged; never release a charged fee."""
    if not tx['release_pending'] or tx['status'] not in {'FAILED', 'EXPIRED', 'CANCELLED'}:
        return
    try:
        ledger = upstream('GET', USER_URL, f"/internal/accounts/{tx['account_id']}/debits/{tx['id']}")
        if ledger['debited']:
            with db() as conn, conn.cursor() as cur:
                cur.execute("UPDATE transactions SET status='REVIEW',failure_reason=%s,updated_at=now() WHERE id=%s",
                            ('Phát hiện ghi nợ trên giao dịch đã kết thúc; cần đối soát', str(tx['id'])))
            return
        upstream('POST', TUITION_URL, f"/internal/tuitions/{tx['tuition_fee_id']}/release",
                 json={'transaction_id': str(tx['id'])})
    except HTTPException:
        log.warning('Cleanup deferred for %s', tx['id'])
        return
    with db() as conn, conn.cursor() as cur:
        cur.execute('UPDATE transactions SET release_pending=FALSE,updated_at=now() WHERE id=%s', (str(tx['id']),))


def expire(tx):
    deadline = tx['otp_expires_at'] if tx['status'] == 'OTP_SENT' else tx['expires_at']
    if tx['status'] in {'INIT', 'PENDING_CONFIRM', 'OTP_SENT'} and deadline <= now():
        set_status(tx['id'], 'EXPIRED', 'Hết thời hạn xác nhận giao dịch hoặc OTP')
        cleanup(load_tx(tx['id']))
        return load_tx(tx['id'])
    return tx


def reserve_payment(tx):
    try:
        upstream('POST', TUITION_URL, f"/internal/tuitions/{tx['tuition_fee_id']}/reserve",
                 json={'transaction_id': str(tx['id']), 'expires_at': tx['expires_at'].isoformat()})
    except HTTPException as exc:
        if exc.status_code != 503:
            set_status(tx['id'], 'FAILED', str(exc.detail))
            cleanup(load_tx(tx['id']))
        raise
    set_status(tx['id'], 'PENDING_CONFIRM')


def notice_success(tx):
    if tx['notification_sent_at']:
        return
    payer = upstream('GET', USER_URL, f"/internal/users/{tx['payer_id']}/account")
    send_email(payer['email'], 'Xac nhan dong hoc phi iBanking',
               f"Thanh toán thành công\nMã giao dịch: {tx['id']}\nMSSV: {tx['student_code']}\n"
               f"Sinh viên: {tx['student_name']}\nHọc kỳ: {tx['semester']}\n"
               f"Số tiền: {int(tx['amount']):,} VND\nThời điểm: {tx['completed_at']}\n", f"receipt-{tx['id']}")
    with db() as conn, conn.cursor() as cur:
        cur.execute('UPDATE transactions SET notification_sent_at=now() WHERE id=%s', (str(tx['id']),))


def run_saga(tx):
    """Caller holds transaction_lock. Retry the same debit/mark-paid ID after interruption."""
    try:
        upstream('POST', TUITION_URL, f"/internal/tuitions/{tx['tuition_fee_id']}/start", json={'transaction_id': str(tx['id'])})
    except HTTPException as exc:
        if exc.status_code == 503:
            return
        ledger = upstream('GET', USER_URL, f"/internal/accounts/{tx['account_id']}/debits/{tx['id']}")
        if ledger['debited']:
            set_status(tx['id'], 'REVIEW', 'Đã ghi nợ nhưng quyền giữ chỗ không hợp lệ')
        else:
            set_status(tx['id'], 'FAILED', str(exc.detail))
            cleanup(load_tx(tx['id']))
        return
    try:
        upstream('POST', USER_URL, f"/internal/accounts/{tx['account_id']}/debit",
                 json={'transaction_id': str(tx['id']), 'payer_id': str(tx['payer_id']), 'amount': int(tx['amount'])})
    except HTTPException as exc:
        if exc.status_code == 422:
            set_status(tx['id'], 'FAILED', 'Số dư không đủ tại thời điểm trừ tiền')
            cleanup(load_tx(tx['id']))
        elif exc.status_code != 503:
            set_status(tx['id'], 'REVIEW', f'Lỗi ghi nợ cần đối soát: {exc.detail}')
        return
    try:
        upstream('POST', TUITION_URL, f"/internal/tuitions/{tx['tuition_fee_id']}/mark-paid", json={'transaction_id': str(tx['id'])})
    except HTTPException as exc:
        if exc.status_code != 503:
            set_status(tx['id'], 'REVIEW', f'Đã ghi nợ; lỗi xác nhận học phí: {exc.detail}')
        return
    set_status(tx['id'], 'SUCCESS')


def recover_one(tx_id, blocking=False):
    with transaction_lock(tx_id, blocking) as acquired:
        if not acquired:
            return
        tx = expire(load_tx(tx_id))
        with db() as conn, conn.cursor() as cur:
            cur.execute("UPDATE transactions SET retry_after=now()+interval '5 seconds' WHERE id=%s", (str(tx_id),))
        try:
            if tx['status'] == 'INIT':
                reserve_payment(tx)
            elif tx['status'] == 'PROCESSING':
                run_saga(tx)
            tx = load_tx(tx_id)
            cleanup(tx)
            if tx['status'] == 'SUCCESS':
                notice_success(tx)
        except (HTTPException, OSError, smtplib.SMTPException):
            log.warning('Recovery deferred for %s', tx_id)


def reconcile_once():
    with db() as conn, conn.cursor() as cur:
        cur.execute('''SELECT id FROM transactions WHERE retry_after<=now() AND (
          status IN ('INIT','PROCESSING') OR
          (status IN ('PENDING_CONFIRM','OTP_SENT') AND
            LEAST(expires_at,COALESCE(otp_expires_at,expires_at))<=now()) OR
          (status IN ('FAILED','EXPIRED','CANCELLED') AND release_pending) OR
          (status='SUCCESS' AND notification_sent_at IS NULL))
          ORDER BY retry_after,created_at LIMIT 50''')
        pending = [r['id'] for r in cur.fetchall()]
    for tx_id in pending:
        try:
            recover_one(tx_id)
        except Exception:
            log.exception('Recovery error for %s', tx_id)


def reconciler_loop():
    while not STOP.wait(3):
        try:
            reconcile_once()
        except Exception:
            log.exception('Recovery database error')


@app.get('/health')
def health():
    with db() as conn, conn.cursor() as cur:
        cur.execute('SELECT 1')
    return {'status': 'ok'}


@app.post('/api/v1/payments', status_code=201, response_model=PaymentView)
def initiate_payment(body: NewPayment, response: Response, current=Depends(require_user),
                     idempotency_key: str | None = Header(default=None)):
    if not idempotency_key or not 8 <= len(idempotency_key) <= 100:
        raise HTTPException(400, 'Idempotency-Key phải dài 8–100 ký tự')
    with db() as conn, conn.cursor() as cur:
        cur.execute('SELECT * FROM transactions WHERE payer_id=%s AND idempotency_key=%s', (current['sub'], idempotency_key))
        original = cur.fetchone()
    if not original:
        payer = upstream('GET', USER_URL, f"/internal/users/{current['sub']}/account")
        fee = upstream('GET', TUITION_URL, f'/internal/tuitions/{body.tuitionFeeId}')
        if fee['status'] == 'PAID':
            raise HTTPException(409, 'Học phí đã được thanh toán')
        if payer['balance'] < fee['amount']:
            raise HTTPException(422, 'Số dư không đủ')
        tx_id = uuid4()
        with db() as conn, conn.cursor() as cur:
            cur.execute('''INSERT INTO transactions(id,payer_id,account_id,tuition_fee_id,student_code,
              student_name,semester,amount,idempotency_key,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT(payer_id,idempotency_key) DO NOTHING''',
                        (str(tx_id), current['sub'], payer['accountId'], str(body.tuitionFeeId), fee['mssv'],
                         fee['studentName'], fee['semester'], fee['amount'], idempotency_key, now()+timedelta(minutes=8)))
            cur.execute('SELECT * FROM transactions WHERE payer_id=%s AND idempotency_key=%s', (current['sub'], idempotency_key))
            original = cur.fetchone()
    if str(original['tuition_fee_id']) != str(body.tuitionFeeId):
        raise HTTPException(409, 'Idempotency-Key đã dùng với khoản học phí khác')
    with transaction_lock(original['id']):
        tx = expire(load_tx(original['id']))
        if tx['status'] == 'INIT':
            reserve_payment(tx)
        tx = load_tx(tx['id'])
        if tx['status'] == 'FAILED':
            raise HTTPException(409, tx['failure_reason'])
        if tx['status'] == 'EXPIRED':
            raise HTTPException(410, 'Giao dịch đã hết hạn; hãy tạo yêu cầu mới')
        if tx['status'] != 'PENDING_CONFIRM':
            response.status_code = 200
        return public_tx(tx)


def otp_result(tx):
    return {'transactionId': str(tx['id']), 'status': tx['status'], 'otpExpiresAt': tx['otp_expires_at'],
            'otpExpiresIn': max(0, int((tx['otp_expires_at']-now()).total_seconds()))}


@app.post('/api/v1/payments/{transaction_id}/confirm', response_model=OTPView)
def confirm_payment(transaction_id: UUID, body: ConfirmBody, current=Depends(require_user)):
    if not body.acceptTerms:
        raise HTTPException(422, 'Cần đồng ý điều khoản trước khi xác nhận')
    with transaction_lock(transaction_id):
        tx = expire(load_tx(transaction_id, current['sub']))
        if tx['status'] == 'EXPIRED':
            raise HTTPException(410, 'Giao dịch hoặc OTP đã hết hạn')
        if tx['status'] == 'OTP_SENT':
            return otp_result(tx)
        if tx['status'] != 'PENDING_CONFIRM':
            raise HTTPException(409, f'Không thể xác nhận giao dịch {tx["status"]}')
        fee = upstream('GET', TUITION_URL, f"/internal/tuitions/{tx['tuition_fee_id']}")
        if fee['reservedBy'] != str(transaction_id) or fee['status'] != 'PENDING_PAYMENT':
            set_status(transaction_id, 'EXPIRED', 'Không còn giữ chỗ khoản học phí')
            cleanup(load_tx(transaction_id))
            raise HTTPException(410, 'Giao dịch không còn giữ chỗ')
        payer = upstream('GET', USER_URL, f"/internal/users/{tx['payer_id']}/account")
        if payer['balance'] < int(tx['amount']):
            set_status(transaction_id, 'FAILED', 'Số dư không đủ tại thời điểm xác nhận')
            cleanup(load_tx(transaction_id))
            raise HTTPException(422, 'Số dư không đủ')
        tx = expire(load_tx(transaction_id))
        if tx['status'] == 'EXPIRED':
            raise HTTPException(410, 'Giao dịch đã hết hạn')
        deadline = min(now()+timedelta(minutes=5), tx['expires_at'])
        with db() as conn, conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(604070)')
            cur.execute('UPDATE otps SET code_fingerprint=NULL WHERE expires_at<=now() OR used')
            for _ in range(100):
                code = f'{secrets.randbelow(1_000_000):06d}'
                fingerprint = hmac.new(OTP_SECRET.encode(), f'active:{code}'.encode(), hashlib.sha256).hexdigest()
                cur.execute('SELECT 1 FROM otps WHERE code_fingerprint=%s', (fingerprint,))
                if not cur.fetchone():
                    break
            else:
                raise HTTPException(503, 'Chưa cấp được OTP, hãy thử lại')
            cur.execute('INSERT INTO otps(transaction_id,code_hmac,code_fingerprint,expires_at) VALUES(%s,%s,%s,%s)',
                        (str(transaction_id), otp_hash(transaction_id, code), fingerprint, deadline))
            cur.execute("""UPDATE transactions SET status='OTP_SENT',otp_expires_at=%s,
              terms_accepted_at=now(),updated_at=now() WHERE id=%s""", (deadline, str(transaction_id)))
        try:
            send_email(payer['email'], 'Ma OTP xac thuc hoc phi iBanking',
                       f'Mã OTP cho giao dịch {transaction_id}: {code}\nHết hạn lúc {deadline.isoformat()} (UTC).\n',
                       f'otp-{transaction_id}')
        except (OSError, smtplib.SMTPException):
            # Revoke on an uncertain SMTP acknowledgement too; the next attempt issues a fresh code.
            with db() as conn, conn.cursor() as cur:
                cur.execute('DELETE FROM otps WHERE transaction_id=%s', (str(transaction_id),))
                cur.execute("UPDATE transactions SET status='PENDING_CONFIRM',otp_expires_at=NULL WHERE id=%s", (str(transaction_id),))
            raise HTTPException(503, 'Chưa xác nhận gửi được OTP; vui lòng thử gửi lại')
        return otp_result(load_tx(transaction_id))


@app.post('/api/v1/payments/{transaction_id}/verify-otp', response_model=PaymentView)
def verify_otp(transaction_id: UUID, body: OTPBody, current=Depends(require_user)):
    with transaction_lock(transaction_id):
        tx = expire(load_tx(transaction_id, current['sub']))
        if tx['status'] == 'EXPIRED':
            raise HTTPException(410, 'OTP hoặc giao dịch đã hết hạn')
        if tx['status'] in {'SUCCESS', 'PROCESSING'}:
            return public_tx(tx)
        if tx['status'] != 'OTP_SENT':
            raise HTTPException(409, f'Không thể xác thực giao dịch {tx["status"]}')
        error = None
        with db() as conn, conn.cursor() as cur:
            cur.execute('SELECT * FROM otps WHERE transaction_id=%s FOR UPDATE', (str(transaction_id),))
            otp = cur.fetchone()
            if not otp or otp['used'] or otp['expires_at'] <= now():
                error = (410, 'OTP đã hết hạn hoặc đã sử dụng')
            elif not hmac.compare_digest(otp['code_hmac'], otp_hash(transaction_id, body.otp)):
                cur.execute('UPDATE otps SET attempts_count=attempts_count+1 WHERE id=%s', (str(otp['id']),))
                error = (429, 'OTP sai 5 lần; giao dịch bị hủy') if otp['attempts_count']+1 >= 5 else (422, 'OTP không chính xác')
            else:
                cur.execute('UPDATE otps SET used=TRUE,code_fingerprint=NULL WHERE id=%s', (str(otp['id']),))
                cur.execute("UPDATE transactions SET status='PROCESSING',processing_started_at=now(),updated_at=now() WHERE id=%s", (str(transaction_id),))
        if error:
            if error[0] in (410, 429):
                set_status(transaction_id, 'EXPIRED' if error[0] == 410 else 'FAILED', error[1])
                cleanup(load_tx(transaction_id))
            raise HTTPException(*error)
        try:
            run_saga(load_tx(transaction_id))
        except HTTPException as exc:
            if exc.status_code != 503:
                raise
        tx = load_tx(transaction_id)
        if tx['status'] == 'SUCCESS':
            try:
                notice_success(tx)
            except (HTTPException, OSError, smtplib.SMTPException):
                log.warning('Receipt deferred for %s', transaction_id)
        if tx['status'] == 'FAILED':
            raise HTTPException(422, tx['failure_reason'])
        return public_tx(tx)


@app.post('/api/v1/payments/{transaction_id}/cancel', response_model=PaymentView)
def cancel(transaction_id: UUID, current=Depends(require_user)):
    with transaction_lock(transaction_id):
        tx = expire(load_tx(transaction_id, current['sub']))
        if tx['status'] not in {'INIT', 'PENDING_CONFIRM', 'OTP_SENT', 'CANCELLED'}:
            raise HTTPException(409, 'Không thể hủy giao dịch đã xử lý hoặc hết hạn')
        set_status(transaction_id, 'CANCELLED', 'Người dùng hủy giao dịch')
        cleanup(load_tx(transaction_id))
        return public_tx(load_tx(transaction_id))


@app.get('/api/v1/payments/{transaction_id}', response_model=PaymentView)
def get_payment(transaction_id: UUID, current=Depends(require_user)):
    with transaction_lock(transaction_id):
        return public_tx(expire(load_tx(transaction_id, current['sub'])))


@app.get('/api/v1/users/me/transactions', response_model=HistoryView)
def transaction_history(page: int = Query(1, ge=1), size: int = Query(10, ge=1, le=50), current=Depends(require_user)):
    with db() as conn, conn.cursor() as cur:
        cur.execute('SELECT COUNT(*) AS total FROM transactions WHERE payer_id=%s', (current['sub'],))
        total = cur.fetchone()['total']
        cur.execute('SELECT * FROM transactions WHERE payer_id=%s ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s',
                    (current['sub'], size, (page-1)*size))
        items = cur.fetchall()
    return {'page': page, 'size': size, 'total': total, 'items': [public_tx(tx) for tx in items]}
