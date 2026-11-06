#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<'SQL'
CREATE DATABASE user_db;
CREATE DATABASE tuition_db;
CREATE DATABASE payment_db;
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname user_db -f /docker-entrypoint-initdb.d/02-user-db.sql
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname tuition_db -f /docker-entrypoint-initdb.d/03-tuition-db.sql
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname payment_db -f /docker-entrypoint-initdb.d/04-payment-db.sql
