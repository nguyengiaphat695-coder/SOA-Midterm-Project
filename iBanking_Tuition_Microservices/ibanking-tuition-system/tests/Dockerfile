FROM python:3.11-slim
WORKDIR /project
COPY services/payment-service/requirements.txt /tmp/service-requirements.txt
RUN pip install --no-cache-dir -r /tmp/service-requirements.txt pytest==9.2.0
COPY . .
CMD ["python", "-m", "pytest", "tests/test_static_contracts.py", "tests/test_payment_unit.py", "tests/test_integration.py", "-v", "-ra"]
