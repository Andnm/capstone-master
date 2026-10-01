@echo off
setlocal EnableExtensions
title AUX Crawler Launcher

set "START_SCRIPT=%~dp0hotel-price-intelligence\start_project.ps1"

if not exist "%START_SCRIPT%" (
    echo [ERROR] Khong tim thay: %START_SCRIPT%
    if /I not "%AUX_CRAWLER_NO_PAUSE%"=="1" pause
    exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%START_SCRIPT%"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%EXIT_CODE%"=="0" (
    echo [ERROR] Khong the khoi dong day du he thong crawl.
    echo Hay chup lai cua so nay de kiem tra loi.
) else (
    echo [OK] He thong da san sang. Ban co the dung trinh duyet de tao crawl run.
)

if /I not "%AUX_CRAWLER_NO_PAUSE%"=="1" pause
exit /b %EXIT_CODE%
