@echo off
cd /d "%~dp0"
echo === iBanking TDTU Demo ===
docker compose up -d --build --wait --wait-timeout 180
if errorlevel 1 (
  echo Docker Compose that bai. Kiem tra Docker Desktop dang chay.
  pause
  exit /b 1
)
echo WEB: http://localhost:8080
echo OTP EMAIL: http://localhost:8025
start "" "http://localhost:8080"
start "" "http://localhost:8025"
pause
