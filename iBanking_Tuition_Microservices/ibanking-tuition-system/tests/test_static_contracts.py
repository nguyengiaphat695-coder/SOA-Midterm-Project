"""Run without Docker: static project contract checks (NOT a substitute for integration)."""
from pathlib import Path
import hashlib
import ast
import re
import hmac
import unittest
from uuid import uuid4

ROOT=Path(__file__).resolve().parent.parent

class StaticContracts(unittest.TestCase):
    def test_python_sources_parse(self):
        for file in ROOT.glob('services/*/main.py'):
            ast.parse(file.read_text(),filename=str(file))

    def test_demo_hashes_match_password(self):
        seed=(ROOT/'database/init/02-user-db.sql').read_text()
        hashes=re.findall(r"pbkdf2_sha256\$600000\$[a-f0-9]{32}\$[a-f0-9]{64}",seed)
        self.assertEqual(len(hashes),4)
        for encoded in hashes:
            _,iterations,salt,digest=encoded.split('$')
            observed=hashlib.pbkdf2_hmac('sha256',b'123456',bytes.fromhex(salt),int(iterations))
            self.assertTrue(hmac.compare_digest(observed,bytes.fromhex(digest)))

    def test_money_and_idempotency_constraints(self):
        user=(ROOT/'database/init/02-user-db.sql').read_text()
        payment=(ROOT/'database/init/04-payment-db.sql').read_text()
        tuition=(ROOT/'database/init/03-tuition-db.sql').read_text()
        self.assertIn('balance >= 0',user)
        self.assertIn('transaction_id UUID PRIMARY KEY',user)
        self.assertIn('UNIQUE(payer_id,idempotency_key)',payment)
        self.assertIn('UNIQUE(student_code,semester)',tuition)
        self.assertIn('paid_by_transaction_id UUID UNIQUE',tuition)

    def test_uuid_values_from_database_match_pydantic_uuid(self):
        # psycopg2 may return a PostgreSQL UUID as plain text, while Pydantic returns uuid.UUID.
        tuition=(ROOT/'services/tuition-service/main.py').read_text()
        tree=ast.parse(tuition)
        normalize=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='same_uuid')
        namespace={}
        exec(compile(ast.Module(body=[normalize],type_ignores=[]),'<uuid_helper>','exec'),namespace)
        uuid_value=uuid4()
        self.assertTrue(namespace['same_uuid'](str(uuid_value),uuid_value))
        self.assertTrue(namespace['same_uuid'](uuid_value,str(uuid_value)))
        self.assertFalse(namespace['same_uuid'](None,None))
        self.assertFalse(namespace['same_uuid'](str(uuid4()),uuid_value))
        self.assertIn("str(a['user_id']) != str(body.payer_id)",(ROOT/'services/user-service/main.py').read_text())

    def test_no_service_port_published(self):
        compose=(ROOT/'docker-compose.yml').read_text()
        self.assertIn('127.0.0.1:8080:80',compose)
        self.assertNotIn('3001:3001',compose)
        self.assertNotIn('3002:3002',compose)
        self.assertNotIn('3003:3003',compose)

if __name__=='__main__':unittest.main()
