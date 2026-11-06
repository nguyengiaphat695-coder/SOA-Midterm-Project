"""Tuition Service: authoritative fees and exclusive, transaction-bound reservation."""
import hmac
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID
import jwt
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, AwareDatetime

app = FastAPI(title='iBanking Tuition Service',version='3.0.0',
             docs_url='/docs/tuition',redoc_url='/redoc/tuition',openapi_url='/openapi/tuition.json')
DB_URL=os.getenv('DATABASE_URL','')
JWT_SECRET=os.getenv('JWT_SECRET','')
INTERNAL_TOKEN=os.getenv('INTERNAL_API_TOKEN','')

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

def require_user(authorization: str | None=Header(default=None)):
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401,'Cần đăng nhập')
    try:
        return jwt.decode(authorization[7:],JWT_SECRET,algorithms=['HS256'],options={'require':['exp','sub']})
    except jwt.InvalidTokenError:
        raise HTTPException(401,'Token không hợp lệ hoặc hết hạn')

def internal(x_service_token: str | None=Header(default=None)):
    if not INTERNAL_TOKEN or not x_service_token or not hmac.compare_digest(INTERNAL_TOKEN,x_service_token):
        raise HTTPException(403,'Không có quyền gọi API nội bộ')

class ReservationBody(BaseModel):
    transaction_id: UUID

class HoldBody(ReservationBody):
    expires_at: AwareDatetime


def same_uuid(db_value, requested_uuid):
    """psycopg2 may return PostgreSQL UUID as str without register_uuid().
    Normalize both sides, but never match missing identifiers.
    """
    return db_value is not None and requested_uuid is not None and str(db_value) == str(requested_uuid)

@app.get('/health')
def health():
    with db() as conn:
        with conn.cursor() as cur: cur.execute('SELECT 1')
    return {'status':'ok'}

@app.get('/api/v1/tuitions/{mssv}',dependencies=[Depends(require_user)])
def get_tuitions(mssv:str):
    with db() as conn:
        with conn.cursor() as cur:
            # Expire only pre-processing holds. PROCESSING is deliberately never auto-released.
            cur.execute('''UPDATE tuition_fees SET status='UNPAID',reserved_by_transaction_id=NULL,
                           reservation_expires_at=NULL,updated_at=now()
                           WHERE student_code=%s AND status='PENDING_PAYMENT' AND reservation_expires_at<=now()''',(mssv,))
            cur.execute('SELECT student_code,full_name,faculty,major FROM students WHERE student_code=%s',(mssv,))
            student=cur.fetchone()
            if not student: raise HTTPException(404,'Không tìm thấy MSSV')
            cur.execute('''SELECT id,semester,amount,status FROM tuition_fees
                           WHERE student_code=%s ORDER BY semester DESC''',(mssv,))
            fees=cur.fetchall()
    return {'student':student,'tuitionFees':[{'id':str(x['id']),'semester':x['semester'],'amount':int(x['amount']),'status':x['status']} for x in fees]}

@app.get('/internal/tuitions/{tuition_id}',dependencies=[Depends(internal)])
def get_fee(tuition_id:UUID):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT t.*,s.full_name AS student_name FROM tuition_fees t
                           JOIN students s ON s.student_code=t.student_code WHERE t.id=%s''',(str(tuition_id),))
            x=cur.fetchone()
            if not x: raise HTTPException(404,'Khoản học phí không tồn tại')
    return {'id':str(x['id']), 'mssv':x['student_code'], 'studentName':x['student_name'], 'semester':x['semester'],'amount':int(x['amount']),'status':x['status'], 'reservedBy':str(x['reserved_by_transaction_id']) if x['reserved_by_transaction_id'] else None,'reservedUntil':x['reservation_expires_at'],'paidBy':str(x['paid_by_transaction_id']) if x['paid_by_transaction_id'] else None}

@app.post('/internal/tuitions/{tuition_id}/reserve',dependencies=[Depends(internal)])
def reserve(tuition_id:UUID,body:HoldBody):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM tuition_fees WHERE id=%s FOR UPDATE',(str(tuition_id),))
            r=cur.fetchone()
            if not r: raise HTTPException(404,'Khoản học phí không tồn tại')
            if r['status']=='PAID': raise HTTPException(409,'Học phí đã được thanh toán')
            if body.expires_at <= datetime.now(timezone.utc):
                raise HTTPException(409,'Giao dịch giữ chỗ đã hết hạn')
            if body.expires_at > datetime.now(timezone.utc)+timedelta(minutes=9):
                raise HTTPException(422,'Thời hạn giữ chỗ không hợp lệ')
            if same_uuid(r['reserved_by_transaction_id'], body.transaction_id) and r['status'] in ('PENDING_PAYMENT','PROCESSING'):
                return {'status':r['status'],'reservedBy':str(body.transaction_id)}
            if r['status']=='PROCESSING':
                raise HTTPException(409,'Đang xử lý giao dịch khác, không thể thanh toán trùng')
            if r['status']=='PENDING_PAYMENT' and r['reservation_expires_at'] and r['reservation_expires_at']>datetime.now(timezone.utc):
                raise HTTPException(409,'Học phí đang được giữ chỗ bởi giao dịch khác')
            until=body.expires_at
            cur.execute('''UPDATE tuition_fees SET status='PENDING_PAYMENT',reserved_by_transaction_id=%s,
                           reservation_expires_at=%s,updated_at=now() WHERE id=%s''',
                        (str(body.transaction_id),until,str(tuition_id)))
    return {'status':'PENDING_PAYMENT','reservedBy':str(body.transaction_id),'expiresAt':until}

@app.post('/internal/tuitions/{tuition_id}/start',dependencies=[Depends(internal)])
def start_processing(tuition_id:UUID,body:ReservationBody):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM tuition_fees WHERE id=%s FOR UPDATE',(str(tuition_id),))
            r=cur.fetchone()
            if not r: raise HTTPException(404,'Không tìm thấy học phí')
            if not same_uuid(r['reserved_by_transaction_id'], body.transaction_id):
                raise HTTPException(409,'Giao dịch không giữ chỗ khoản học phí này')
            if r['status']=='PROCESSING': return {'status':'PROCESSING'}
            if r['status']=='PAID' and same_uuid(r['paid_by_transaction_id'], body.transaction_id): return {'status':'PAID'}
            if r['status']!='PENDING_PAYMENT' or not r['reservation_expires_at'] or r['reservation_expires_at']<=datetime.now(timezone.utc):
                raise HTTPException(409,'Hết hạn giữ chỗ, vui lòng thanh toán lại')
            # PROCESSING does not automatically expire: recovery must finish or operator resolves.
            cur.execute("UPDATE tuition_fees SET status='PROCESSING',reservation_expires_at=NULL,updated_at=now() WHERE id=%s",(str(tuition_id),))
    return {'status':'PROCESSING'}

@app.post('/internal/tuitions/{tuition_id}/mark-paid',dependencies=[Depends(internal)])
def mark_paid(tuition_id:UUID,body:ReservationBody):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM tuition_fees WHERE id=%s FOR UPDATE',(str(tuition_id),))
            r=cur.fetchone()
            if not r: raise HTTPException(404,'Khoản học phí không tồn tại')
            if r['status']=='PAID' and same_uuid(r['paid_by_transaction_id'], body.transaction_id):
                return {'status':'PAID','transactionId':str(body.transaction_id)}
            if r['status']!='PROCESSING' or not same_uuid(r['reserved_by_transaction_id'], body.transaction_id):
                raise HTTPException(409,'Chỉ giao dịch đang xử lý và giữ chỗ mới được ghi nhận đã thanh toán')
            cur.execute('''UPDATE tuition_fees SET status='PAID',paid_by_transaction_id=%s,
                           reservation_expires_at=NULL,updated_at=now() WHERE id=%s''',
                        (str(body.transaction_id),str(tuition_id)))
    return {'status':'PAID','transactionId':str(body.transaction_id)}

@app.post('/internal/tuitions/{tuition_id}/release',dependencies=[Depends(internal)])
def release(tuition_id:UUID,body:ReservationBody):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT status,reserved_by_transaction_id FROM tuition_fees WHERE id=%s FOR UPDATE',(str(tuition_id),))
            r=cur.fetchone()
            if not r: raise HTTPException(404,'Khoản học phí không tồn tại')
            if not same_uuid(r['reserved_by_transaction_id'], body.transaction_id):
                return {'status':'NO_CHANGE'}
            if r['status']=='PAID':
                raise HTTPException(409,'Không được mở lại học phí đã thanh toán')
            if same_uuid(r['reserved_by_transaction_id'], body.transaction_id):
                cur.execute('''UPDATE tuition_fees SET status='UNPAID',reserved_by_transaction_id=NULL,
                               reservation_expires_at=NULL,updated_at=now() WHERE id=%s''',(str(tuition_id),))
                return {'status':'UNPAID'}
    return {'status':'NO_CHANGE'}
