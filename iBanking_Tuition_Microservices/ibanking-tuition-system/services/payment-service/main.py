"""Payment orchestration: OTP email, idempotent debit, transaction-bound tuition commit, recovery."""
import hashlib
import hmac
import logging
import os
import secrets
import smtplib
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from uuid import UUID, uuid4

import httpx
import jwt
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict

app = FastAPI(title='iBanking Payment Orchestrator', version='2.0.0')
log = logging.getLogger('payments')
DB_URL = os.getenv('DATABASE_URL','')
USER_URL = os.getenv('USER_SERVICE_URL','http://user-service:3001')
TUITION_URL = os.getenv('TUITION_SERVICE_URL','http://tuition-service:3002')
JWT_SECRET = os.getenv('JWT_SECRET','')
INTERNAL_TOKEN = os.getenv('INTERNAL_API_TOKEN','')
OTP_SECRET = os.getenv('OTP_SECRET','')
SMTP_HOST = os.getenv('SMTP_HOST','mailpit')
SMTP_PORT = int(os.getenv('SMTP_PORT','1025'))
SMTP_FROM = os.getenv('SMTP_FROM','ibanking@demo.local')
SMTP_USERNAME = os.getenv('SMTP_USERNAME','')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD','')
SMTP_STARTTLS = os.getenv('SMTP_STARTTLS','false').lower()=='true'
stop_reconciler=threading.Event()

@contextmanager
def db():
    conn=psycopg2.connect(DB_URL,cursor_factory=RealDictCursor)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()

def require_user(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401,'Cần đăng nhập')
    try:
        claims=jwt.decode(authorization[7:],JWT_SECRET,algorithms=['HS256'],options={'require':['exp','sub']})
        UUID(claims['sub'])
        return claims
    except (jwt.InvalidTokenError,KeyError,ValueError):
        raise HTTPException(401,'Token không hợp lệ hoặc đã hết hạn')

def internal_headers():
    return {'X-Service-Token':INTERNAL_TOKEN}

def upstream(method,base,path,**kwargs):
    try:
        with httpx.Client(timeout=5.0,headers=internal_headers()) as client:
            response=client.request(method,base+path,**kwargs)
    except httpx.RequestError as exc:
        log.warning('Microservice unavailable: %s %s: %s',method,path,type(exc).__name__)
        raise HTTPException(503,'Không kết nối được dịch vụ nội bộ; giao dịch sẽ được đối soát')
    if response.status_code>=400:
        try: message=response.json().get('detail','Lỗi xử lý liên dịch vụ')
        except ValueError: message='Lỗi xử lý liên dịch vụ'
        raise HTTPException(response.status_code,message)
    return response.json()

def send_email(to,subject,body):
    msg=EmailMessage()
    msg['From']=SMTP_FROM
    msg['To']=to
    msg['Subject']=subject
    msg.set_content(body)
    with smtplib.SMTP(SMTP_HOST,SMTP_PORT,timeout=5) as smtp:
        if SMTP_STARTTLS:
            smtp.starttls()
        if SMTP_USERNAME:
            smtp.login(SMTP_USERNAME,SMTP_PASSWORD)
        smtp.send_message(msg)

def otp_hash(tx_id,code):
    # Never store or log the OTP. Keyed HMAC prevents offline brute-force from a DB leak.
    return hmac.new(OTP_SECRET.encode(),f'{tx_id}:{code}'.encode(),hashlib.sha256).hexdigest()

class NewPayment(BaseModel):
    model_config=ConfigDict(extra='forbid')
    tuitionFeeId: UUID

class OTPBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    otp: str = Field(pattern=r'^\d{6}$')

class PaymentStatus(BaseModel):
    status: str

def public_tx(tx):
    return {'transactionId':str(tx['id']),'payerId':str(tx['payer_id']),
            'mssv':tx['student_code'],'studentName':tx['student_name'],
            'semester':tx['semester'],'amount':int(tx['amount']),
            'status':tx['status'],'failureReason':tx['failure_reason'],
            'createdAt':tx['created_at'],'completedAt':tx['completed_at']}

def load_tx(tx_id, payer_id=None, for_update=False):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM transactions WHERE id=%s'+(' FOR UPDATE' if for_update else ''),(str(tx_id),))
            tx=cur.fetchone()
    if not tx: raise HTTPException(404,'Không tìm thấy giao dịch')
    if payer_id and str(tx['payer_id'])!=str(payer_id): raise HTTPException(403,'Không phải chủ giao dịch')
    return tx

def update_status(tx_id,status,reason=None):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''UPDATE transactions SET status=%s,failure_reason=%s,
                completed_at=CASE WHEN %s IN ('SUCCESS','FAILED','EXPIRED','CANCELLED') THEN now() ELSE completed_at END,
                updated_at=now() WHERE id=%s''',(status,reason,status,str(tx_id)))

def release_hold(tx):
    try:
        upstream('POST',TUITION_URL,f"/internal/tuitions/{tx['tuition_fee_id']}/release",json={'transaction_id':str(tx['id'])})
    except HTTPException as exc:
        log.warning('Release failed for %s: %s',tx['id'],exc.detail)

def notice_success(tx):
    if tx['notification_sent_at'] is not None: return
    payer=upstream('GET',USER_URL,f"/internal/users/{tx['payer_id']}/account")
    send_email(payer['email'],'Xac nhan dong hoc phi iBanking',
               f"Thanh toan thanh cong\nMa giao dich: {tx['id']}\nMSSV: {tx['student_code']}\nHoc ky: {tx['semester']}\nSo tien: {int(tx['amount']):,} VND\n")
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('UPDATE transactions SET notification_sent_at=now() WHERE id=%s AND notification_sent_at IS NULL',(str(tx['id']),))

def run_saga(tx_id):
    """Retry-safe: start -> idempotent debit -> idempotent mark-paid -> SUCCESS."""
    tx=load_tx(tx_id)
    if tx['status']=='SUCCESS':
        try: notice_success(tx)
        except (HTTPException,OSError,smtplib.SMTPException): log.warning('Receipt delivery will be retried for %s',tx_id)
        return 'SUCCESS'
    if tx['status']!='PROCESSING': return tx['status']
    try:
        upstream('POST',TUITION_URL,f"/internal/tuitions/{tx['tuition_fee_id']}/start",json={'transaction_id':str(tx_id)})
    except HTTPException as exc:
        if exc.status_code==409:
            # No money has been debited by this path if /start has never succeeded.
            # On recovery, check ledger to avoid marking FAILED after an uncertain call.
            try:
                debit=upstream('GET',USER_URL,f"/internal/accounts/{tx['account_id']}/debits/{tx_id}")
                if debit['debited']:
                    update_status(tx_id,'REVIEW','Reservation conflict after debit; manual reconciliation required')
                    return 'REVIEW'
            except HTTPException:
                return 'PROCESSING'
            update_status(tx_id,'FAILED','Giữ chỗ học phí không hợp lệ/hết hạn')
            release_hold(tx)
            return 'FAILED'
        if exc.status_code!=503:
            update_status(tx_id,'REVIEW',f'Tuition start error: {exc.detail}')
            return 'REVIEW'
        return 'PROCESSING'
    try:
        upstream('POST',USER_URL,f"/internal/accounts/{tx['account_id']}/debit",
                 json={'transaction_id':str(tx_id),'payer_id':str(tx['payer_id']),'amount':int(tx['amount'])})
    except HTTPException as exc:
        if exc.status_code==422: # explicit insufficient balance, definitely not charged
            update_status(tx_id,'FAILED','Số dư không đủ tại thời điểm trừ tiền')
            release_hold(tx)
            return 'FAILED'
        if exc.status_code!=503:
            update_status(tx_id,'REVIEW',f'Account debit rejected: {exc.detail}')
            return 'REVIEW'
        return 'PROCESSING'
    try:
        upstream('POST',TUITION_URL,f"/internal/tuitions/{tx['tuition_fee_id']}/mark-paid",json={'transaction_id':str(tx_id)})
    except HTTPException as exc:
        if exc.status_code!=503:
            update_status(tx_id,'REVIEW',f'Debited; tuition update failed: {exc.detail}')
            return 'REVIEW'
        return 'PROCESSING'
    # Already-debited retries cannot debit twice due to account_debits UNIQUE transaction_id.
    update_status(tx_id,'SUCCESS')
    try:
        notice_success(load_tx(tx_id))
    except (HTTPException,OSError,smtplib.SMTPException):
        log.warning('Payment successful; receipt deferred for %s',tx_id)
    return 'SUCCESS'

def reconciler_loop():
    # Basic self-healing for interrupted HTTP calls; for actual banking use a durable queue/worker.
    while not stop_reconciler.wait(15):
        try:
            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM transactions WHERE status='PROCESSING' AND processing_started_at < now()-interval '12 seconds' ORDER BY processing_started_at LIMIT 30")
                    pending=[r['id'] for r in cur.fetchall()]
                    cur.execute("SELECT id FROM transactions WHERE status='SUCCESS' AND notification_sent_at IS NULL ORDER BY created_at LIMIT 10")
                    pending.extend(r['id'] for r in cur.fetchall())
            for tx_id in pending:
                try: run_saga(tx_id)
                except Exception:
                    log.exception('Reconciliation cycle failed for %s',tx_id)
        except Exception:
            log.exception('Reconciliation cycle database error')

@app.on_event('startup')
def start_reconciler():
    stop_reconciler.clear()
    threading.Thread(target=reconciler_loop,name='payment-reconciler',daemon=True).start()

@app.on_event('shutdown')
def shutdown_reconciler():
    stop_reconciler.set()

@app.get('/health')
def health():
    with db() as conn:
        with conn.cursor() as cur: cur.execute('SELECT 1')
    return {'status':'ok'}

@app.post('/api/v1/payments',status_code=201)
def initiate_payment(body:NewPayment, current=Depends(require_user),idempotency_key:str | None=Header(default=None)):
    if not idempotency_key or not 8<=len(idempotency_key)<=100:
        raise HTTPException(400,'Thiếu hoặc Idempotency-Key không hợp lệ (8–100 ký tự)')
    # A retransmission with the same key returns the *original* transaction, even after it has PAID the fee.
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM transactions WHERE payer_id=%s AND idempotency_key=%s',(current['sub'],idempotency_key))
            original=cur.fetchone()
    if original:
        if str(original['tuition_fee_id'])!=str(body.tuitionFeeId):
            raise HTTPException(409,'Idempotency-Key đã dùng với khoản học phí khác')
        return public_tx(original)
    payer=upstream('GET',USER_URL,f"/internal/users/{current['sub']}/account")
    fee=upstream('GET',TUITION_URL,f'/internal/tuitions/{body.tuitionFeeId}')
    if fee['status']=='PAID': raise HTTPException(409,'Học phí đã được thanh toán')
    if payer['balance']<fee['amount']: raise HTTPException(422,'Số dư không đủ tại thời điểm khởi tạo')
    tx_id=uuid4()
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''INSERT INTO transactions(id,payer_id,account_id,tuition_fee_id,student_code,student_name,semester,amount,idempotency_key,status)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'INIT') ON CONFLICT(payer_id,idempotency_key) DO NOTHING RETURNING id''',
                (str(tx_id),current['sub'],payer['accountId'],str(body.tuitionFeeId),fee['mssv'],fee['studentName'],fee['semester'],fee['amount'],idempotency_key))
            inserted=cur.fetchone()
            if not inserted:
                cur.execute('SELECT * FROM transactions WHERE payer_id=%s AND idempotency_key=%s',(current['sub'],idempotency_key))
                prev=cur.fetchone()
                if str(prev['tuition_fee_id'])!=str(body.tuitionFeeId): raise HTTPException(409,'Idempotency-Key đã dùng với khoản học phí khác')
                return public_tx(prev)
    try:
        upstream('POST',TUITION_URL,f'/internal/tuitions/{body.tuitionFeeId}/reserve',json={'transaction_id':str(tx_id)})
    except HTTPException as exc:
        update_status(tx_id,'FAILED',str(exc.detail))
        release_hold(load_tx(tx_id))  # safe even if reserve succeeded but HTTP reply was lost
        raise
    update_status(tx_id,'PENDING_CONFIRM')
    return public_tx(load_tx(tx_id))

@app.post('/api/v1/payments/{transaction_id}/confirm')
def confirm_payment(transaction_id:UUID,current=Depends(require_user)):
    tx=load_tx(transaction_id,current['sub'])
    if tx['status']=='OTP_SENT': return {'transactionId':str(transaction_id),'status':'OTP_SENT','otpExpiresIn':300}
    if tx['status']!='PENDING_CONFIRM': raise HTTPException(409,f'Không thể xác nhận giao dịch trạng thái {tx["status"]}')
    fee=upstream('GET',TUITION_URL,f"/internal/tuitions/{tx['tuition_fee_id']}")
    if (fee['reservedBy']!=str(transaction_id) or fee['status']!='PENDING_PAYMENT'
            or not fee.get('reservedUntil') or datetime.fromisoformat(fee['reservedUntil'].replace('Z','+00:00'))<=datetime.now(timezone.utc)):
        update_status(transaction_id,'EXPIRED','Đã hết quyền giữ chỗ học phí')
        raise HTTPException(410,'Giao dịch không còn giữ chỗ; hãy tạo giao dịch mới')
    payer=upstream('GET',USER_URL,f"/internal/users/{tx['payer_id']}/account")
    # Serialize OTP issuance; a unique HMAC fingerprint prevents collisions among active codes.
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(604070)')
            cur.execute('SELECT status FROM transactions WHERE id=%s FOR UPDATE',(str(transaction_id),))
            current=cur.fetchone()
            if current['status']!='PENDING_CONFIRM':
                raise HTTPException(409,'Trạng thái giao dịch đã thay đổi')
            cur.execute('UPDATE otps SET code_fingerprint=NULL WHERE expires_at<=now() OR used=TRUE')
            for _ in range(25):
                code=f'{secrets.randbelow(1_000_000):06d}'
                fingerprint=hmac.new(OTP_SECRET.encode(),f'active:{code}'.encode(),hashlib.sha256).hexdigest()
                cur.execute('SELECT 1 FROM otps WHERE code_fingerprint=%s',(fingerprint,))
                if not cur.fetchone(): break
            else:
                raise HTTPException(503,'Không cấp được OTP duy nhất, thử lại sau')
            cur.execute('''INSERT INTO otps(transaction_id,code_hmac,code_fingerprint,expires_at)
                  VALUES(%s,%s,%s,%s) ON CONFLICT(transaction_id) DO UPDATE
                  SET code_hmac=EXCLUDED.code_hmac,code_fingerprint=EXCLUDED.code_fingerprint,
                      expires_at=EXCLUDED.expires_at,used=FALSE,attempts_count=0''',
                  (str(transaction_id),otp_hash(transaction_id,code),fingerprint,
                   datetime.now(timezone.utc)+timedelta(minutes=5)))
            cur.execute("UPDATE transactions SET status='OTP_SENT',updated_at=now() WHERE id=%s",(str(transaction_id),))
    try:
        send_email(payer['email'],'Ma OTP xac thuc hoc phi iBanking',
                   f'Ma OTP cho giao dich {transaction_id}: {code}\nHet han sau 5 phut. Khong chia se ma nay.\n')
    except (OSError,smtplib.SMTPException):
        log.exception('OTP email failed for transaction %s',transaction_id)
        # Revoke the issued OTP on SMTP error and enable resend.
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT status FROM transactions WHERE id=%s FOR UPDATE',(str(transaction_id),))
                if cur.fetchone()['status']=='OTP_SENT':
                    cur.execute('DELETE FROM otps WHERE transaction_id=%s',(str(transaction_id),))
                    cur.execute("UPDATE transactions SET status='PENDING_CONFIRM' WHERE id=%s",(str(transaction_id),))
        raise HTTPException(503,'Không gửi được OTP. Kiểm tra Mailpit/SMTP rồi thử lại')
    return {'transactionId':str(transaction_id),'status':'OTP_SENT','otpExpiresIn':300}

@app.post('/api/v1/payments/{transaction_id}/verify-otp')
def verify_otp(transaction_id:UUID,body:OTPBody,current=Depends(require_user)):
    error=None
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM transactions WHERE id=%s FOR UPDATE',(str(transaction_id),))
            tx=cur.fetchone()
            if not tx: raise HTTPException(404,'Giao dịch không tồn tại')
            if str(tx['payer_id'])!=current['sub']: raise HTTPException(403,'Không phải chủ giao dịch')
            if tx['status']=='SUCCESS': return public_tx(tx)
            if tx['status']=='PROCESSING': return {'transactionId':str(transaction_id),'status':'PROCESSING'}
            if tx['status']!='OTP_SENT': raise HTTPException(409,f'Trạng thái {tx["status"]} không cho phép dùng OTP')
            cur.execute('SELECT * FROM otps WHERE transaction_id=%s FOR UPDATE',(str(transaction_id),))
            otp=cur.fetchone()
            if not otp or otp['used'] or otp['expires_at']<=datetime.now(timezone.utc):
                error=(410,'OTP không tồn tại, đã dùng hoặc hết hạn')
                cur.execute("UPDATE transactions SET status='EXPIRED',failure_reason='OTP expired' WHERE id=%s",(str(transaction_id),))
            elif otp['attempts_count']>=5:
                error=(429,'Vượt quá số lần thử OTP')
                cur.execute("UPDATE transactions SET status='FAILED',failure_reason='Too many OTP attempts' WHERE id=%s",(str(transaction_id),))
            elif not hmac.compare_digest(otp['code_hmac'],otp_hash(transaction_id,body.otp)):
                cur.execute('UPDATE otps SET attempts_count=attempts_count+1 WHERE id=%s',(str(otp['id']),))
                if otp['attempts_count']+1>=5:
                    error=(429,'OTP sai quá 5 lần; giao dịch bị hủy')
                    cur.execute("UPDATE transactions SET status='FAILED',failure_reason='Too many OTP attempts' WHERE id=%s",(str(transaction_id),))
                else: error=(422,'Mã OTP không chính xác')
            else:
                cur.execute('UPDATE otps SET used=TRUE,code_fingerprint=NULL WHERE id=%s',(str(otp['id']),))
                cur.execute("UPDATE transactions SET status='PROCESSING',processing_started_at=now(),updated_at=now() WHERE id=%s",(str(transaction_id),))
    if error:
        if error[0] in (410,429): release_hold(tx)
        raise HTTPException(error[0],error[1])
    status=run_saga(transaction_id)
    if status=='SUCCESS': return public_tx(load_tx(transaction_id))
    if status=='FAILED': raise HTTPException(422,'Thanh toán thất bại, vui lòng kiểm tra số dư/trạng thái học phí')
    if status=='REVIEW': return {'transactionId':str(transaction_id),'status':'REVIEW','message':'Giao dịch cần đối soát; không thanh toán lần nữa'}
    return {'transactionId':str(transaction_id),'status':'PROCESSING','message':'Đang xử lý/khôi phục; không thực hiện thanh toán lần nữa'}

@app.post('/api/v1/payments/{transaction_id}/cancel')
def cancel(transaction_id:UUID,current=Depends(require_user)):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM transactions WHERE id=%s FOR UPDATE',(str(transaction_id),))
            tx=cur.fetchone()
            if not tx: raise HTTPException(404,'Không tìm thấy giao dịch')
            if str(tx['payer_id'])!=current['sub']: raise HTTPException(403,'Không phải chủ giao dịch')
            if tx['status'] not in ('PENDING_CONFIRM','OTP_SENT'):
                raise HTTPException(409,'Không được hủy giao dịch đang xử lý hoặc đã hoàn tất')
            cur.execute("UPDATE transactions SET status='CANCELLED',completed_at=now() WHERE id=%s",(str(transaction_id),))
    release_hold(tx)
    return {'transactionId':str(transaction_id),'status':'CANCELLED'}

@app.get('/api/v1/payments/{transaction_id}')
def get_payment(transaction_id:UUID,current=Depends(require_user)):
    return public_tx(load_tx(transaction_id,current['sub']))

@app.get('/api/v1/users/me/transactions')
def transaction_history(page:int=Query(1,ge=1),size:int=Query(10,ge=1,le=50),current=Depends(require_user)):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) AS total FROM transactions WHERE payer_id=%s',(current['sub'],))
            total=cur.fetchone()['total']
            cur.execute('''SELECT * FROM transactions WHERE payer_id=%s ORDER BY created_at DESC
                           LIMIT %s OFFSET %s''',(current['sub'],size,(page-1)*size))
            items=cur.fetchall()
    return {'page':page,'size':size,'total':total,'items':[public_tx(x) for x in items]}
