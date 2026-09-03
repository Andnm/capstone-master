@echo off
setlocal
chcp 65001 >nul
title Hotel Crawl Progress

set "PROJECT_DIR=%~dp0"
set "BACKEND_DIR=%PROJECT_DIR%backend"
set "PYTHON_EXE=%BACKEND_DIR%\venv\Scripts\python.exe"
set "MONITOR_SCRIPT=%BACKEND_DIR%\scripts\check_crawl_progress.py"

if not exist "%PYTHON_EXE%" (
    echo Khong tim thay Python: %PYTHON_EXE%
    goto :failed
)

if not exist "%MONITOR_SCRIPT%" (
    echo Khong tim thay script: %MONITOR_SCRIPT%
    goto :failed
)

pushd "%BACKEND_DIR%"
"%PYTHON_EXE%" "%MONITOR_SCRIPT%"
set "MONITOR_EXIT_CODE=%ERRORLEVEL%"
popd

echo.
if not "%MONITOR_EXIT_CODE%"=="0" (
    echo Khong the doc tien do crawl. Ma loi: %MONITOR_EXIT_CODE%
)

if /I not "%~1"=="--no-pause" pause
exit /b %MONITOR_EXIT_CODE%

:failed
echo.
if /I not "%~1"=="--no-pause" pause
exit /b 1
