#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
docker compose up -d --build --wait --wait-timeout 180
printf 'WEB: http://localhost:8080\nOTP EMAIL: http://localhost:8025\n'
