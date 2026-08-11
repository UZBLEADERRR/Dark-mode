@echo off
rem Start Sarideo on this machine. Windows; macOS and Linux have start.sh.
rem
rem   start.bat           start it
rem   start.bat flow      start it with the Flow Agent backend as well
rem   start.bat stop      stop it
rem   start.bat update    pull the latest code and rebuild
setlocal enabledelayedexpansion
cd /d "%~dp0"

docker compose version >nul 2>&1
if errorlevel 1 (
  echo Docker topilmadi.
  echo Docker Desktop'ni o'rnating: https://docs.docker.com/get-docker/
  exit /b 1
)

if not exist ".env" (
  copy /y ".env.example" ".env" >nul
  echo.
  echo   .env yaratildi. Kalitlaringizni shu faylga yozing ^(yoki ilova ichida,
  echo   Kutubxona -^> API kalitlari dan qo'shing^) va qaytadan ishga tushiring.
  echo.
)

if /i "%~1"=="stop" ( docker compose --profile flow down & exit /b 0 )
if /i "%~1"=="down" ( docker compose --profile flow down & exit /b 0 )
if /i "%~1"=="update" (
  git pull --ff-only
  docker compose build --pull
)

set PROFILE=
if /i "%~1"=="flow" set PROFILE=--profile flow

docker compose %PROFILE% up -d --build
if errorlevel 1 exit /b 1

set PORT=8000
for /f "tokens=2 delims==" %%A in ('findstr /b /c:"PORT=" .env 2^>nul') do set PORT=%%A

rem The address a phone on the same Wi-Fi can reach.
set LAN=
for /f "tokens=2 delims=:" %%A in ('ipconfig ^| findstr /c:"IPv4"') do (
  if not defined LAN set LAN=%%A
)
if defined LAN set LAN=%LAN: =%

echo.
echo   Sarideo ishlayapti.
echo   Shu noutbukda:  http://localhost:%PORT%
if defined LAN echo   Telefonda:      http://%LAN%:%PORT%   ^(bir xil Wi-Fi'da^)
if /i "%~1"=="flow" echo   Flow Agent:     http://localhost:8001
echo.
echo   Jurnal:     docker compose logs -f sarideo
echo   To'xtatish: start.bat stop
echo   Hamma fayllar: .\data
