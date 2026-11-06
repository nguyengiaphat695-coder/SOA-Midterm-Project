"""User & Account Service: identity, account ownership and idempotent debits."""
import hashlib
import hmac
import os
from contextlib import contextmanager
from decimal import Decimal
from uuid import UUID

import jwt
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title='iBanking User & Account Service', version='2.0.0')
DB_URL = os.environ.get('DATABASE_URL', '')
JWT_SECRET = os.environ.get('JWT_SECRET', '')
INTERNAL_TOKEN = os.environ.get('INTERNAL_API_TOKEN', '')

@contextmanager
def db():
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()

def secure_password_verify(password: str, encoded: str) -> bool:
    """PBKDF2-HMAC-SHA256 with explicit per-user salt and constant-time compare."""
    try:
        algorithm, iterations, salt, digest = encoded.split('$')
        if algorithm != 'pbkdf2_sha256' or int(iterations) < 600_000:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(actual, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False

def require_user(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, 'Cần đăng nhập')
    try:
        payload = jwt.decode(authorization[7:], JWT_SECRET, algorithms=['HS256'], options={'require':['exp','sub']})
        UUID(payload['sub'])
        return payload
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise HTTPException(401, 'Phiên đăng nhập không hợp lệ hoặc đã hết hạn')

def require_internal(x_service_token: str | None = Header(default=None)):
    if not INTERNAL_TOKEN or not x_service_token or not hmac.compare_digest(x_service_token, INTERNAL_TOKEN):
        raise HTTPException(403, 'Chỉ các service được ủy quyền mới gọi được API này')

class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=200)

class DebitBody(BaseModel):
    transaction_id: UUID
    payer_id: UUID
    amount: int = Field(gt=0)

@app.get('/health')
def health():
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT 1')
    return {'status':'ok'}

@app.post('/api/v1/auth/login')
def login(body: LoginBody):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM users WHERE username=%s OR email=%s', (body.username, body.username))
            user = cur.fetchone()
            if not user or not secure_password_verify(body.password, user['password_hash']):
                raise HTTPException(401, 'Username hoặc mật khẩu không chính xác')
            cur.execute('SELECT id,account_number,balance FROM accounts WHERE user_id=%s AND is_default', (user['id'],))
            account = cur.fetchone()
            if not account:
                raise HTTPException(409, 'Người dùng chưa có tài khoản mặc định')
    import datetime
    token = jwt.encode({'sub':str(user['id']), 'exp':datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(hours=1)}, JWT_SECRET, algorithm='HS256')
    return {'accessToken':token, 'expiresIn':3600, 'user':{'id':str(user['id']), 'username':user['username'], 'fullName':user['full_name'], 'email':user['email'], 'phone':user['phone'], 'account':{'id':str(account['id']), 'accountNumber':account['account_number'], 'balance':int(account['balance'])}}}

@app.get('/api/v1/users/me')
def get_profile(current=Depends(require_user)):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT u.id,u.username,u.full_name,u.phone,u.email,a.id AS account_id,a.account_number,a.balance
                       FROM users u JOIN accounts a ON a.user_id=u.id AND a.is_default
                       WHERE u.id=%s''', (current['sub'],))
            r = cur.fetchone()
            if not r:
                raise HTTPException(404,'Không tìm thấy tài khoản')
    return {'id':str(r['id']), 'username':r['username'], 'fullName':r['full_name'], 'phone':r['phone'], 'email':r['email'], 'balance':int(r['balance']), 'account':{'id':str(r['account_id']), 'accountNumber':r['account_number']}}

@app.get('/internal/users/{payer_id}/account', dependencies=[Depends(require_internal)])
def internal_account(payer_id: UUID):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('''SELECT u.id AS payer_id,u.full_name,u.email,a.id AS account_id,a.balance
                       FROM users u JOIN accounts a ON a.user_id=u.id AND a.is_default WHERE u.id=%s''', (str(payer_id),))
            r = cur.fetchone()
            if not r:
                raise HTTPException(404,'Không tồn tại tài khoản nguồn')
    return {'payerId':str(r['payer_id']), 'fullName':r['full_name'], 'email':r['email'], 'accountId':str(r['account_id']), 'balance':int(r['balance'])}

@app.post('/internal/accounts/{account_id}/debit', dependencies=[Depends(require_internal)])
def debit(account_id: UUID, body: DebitBody):
    """Concurrent transactions serialize on account row; same tx ID charged at most once."""
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id,user_id,balance FROM accounts WHERE id=%s FOR UPDATE',(str(account_id),))
            a = cur.fetchone()
            if not a:
                raise HTTPException(404,'Tài khoản không tồn tại')
            if str(a['user_id']) != str(body.payer_id):
                raise HTTPException(403,'Tài khoản không thuộc người nộp tiền')
            # Reserve the unique debit ID BEFORE updating money. On conflict, check ownership/amount.
            cur.execute('''INSERT INTO account_debits(transaction_id,account_id,payer_id,amount)
                     VALUES (%s,%s,%s,%s) ON CONFLICT(transaction_id) DO NOTHING RETURNING transaction_id''',
                     (str(body.transaction_id),str(account_id),str(body.payer_id),body.amount))
            inserted = cur.fetchone()
            if inserted is None:
                cur.execute('SELECT account_id,payer_id,amount FROM account_debits WHERE transaction_id=%s',(str(body.transaction_id),))
                prior = cur.fetchone()
                if prior is None or str(prior['account_id']) != str(account_id) or str(prior['payer_id']) != str(body.payer_id) or prior['amount'] != Decimal(body.amount):
                    raise HTTPException(409,'Transaction ID đã được dùng cho giao dịch khác')
                return {'status':'ALREADY_DEBITED','newBalance':int(a['balance'])}
            cur.execute('''UPDATE accounts SET balance=balance-%s,updated_at=now() WHERE id=%s AND balance >= %s
                           RETURNING balance''', (body.amount,str(account_id),body.amount))
            result = cur.fetchone()
            if not result:
                raise HTTPException(422, 'Số dư không đủ để thanh toán')
            return {'status':'DEBITED','newBalance':int(result['balance'])}

@app.get('/internal/accounts/{account_id}/debits/{transaction_id}', dependencies=[Depends(require_internal)])
def lookup_debit(account_id: UUID, transaction_id: UUID):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT amount FROM account_debits WHERE account_id=%s AND transaction_id=%s',(str(account_id),str(transaction_id)))
            found=cur.fetchone()
    return {'debited':bool(found),'amount':int(found['amount']) if found else None}
