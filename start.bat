@echo off
chcp 65001 >nul 2>&1
title QAZAN MUSIC - Launcher
setlocal EnableDelayedExpansion

:: ─── ANSI colors ───
for /F "delims=" %%E in ('echo prompt $E^| cmd') do set "ESC=%%E"
set "O=%ESC%[38;5;208m"
set "G=%ESC%[38;5;245m"
set "GR=%ESC%[38;5;82m"
set "R=%ESC%[38;5;196m"
set "C=%ESC%[38;5;51m"
set "B=%ESC%[1m"
set "X=%ESC%[0m"

cls
echo.
echo   %O%╔══════════════════════════════════════════════════╗%X%
echo   %O%║%X%   %B%%O%Q A Z A N%X%  %G%·%X%  %B%MP3 MUSIC DOWNLOADER%X%       %O%║%X%
echo   %O%╚══════════════════════════════════════════════════╝%X%
echo.

cd /d "%~dp0"

:: ─── Python тексеру ───
where python >nul 2>&1
if errorlevel 1 (
    echo   %R%[!]%X% Python табылмады. https://python.org сайтынан орнатыңыз.
    pause
    exit /b 1
)

:: ─── venv тексеру ───
if not exist ".venv\Scripts\activate.bat" (
    echo   %C%[*]%X% Виртуалды орта жасалуда...
    python -m venv .venv
    if errorlevel 1 (
        echo   %R%[!]%X% Виртуалды орта жасалмады.
        pause
        exit /b 1
    )
    echo   %GR%[+]%X% Виртуалды орта дайын.
    echo.
    call .venv\Scripts\activate.bat
    echo   %C%[*]%X% Кітапханалар орнатылуда...
    if exist "requirements.txt" (
        pip install -q -r requirements.txt
    ) else (
        pip install -q aiogram fastapi uvicorn yt-dlp aiosqlite jinja2 python-multipart colorama websockets pydantic yt-dlp-ejs
    )
    echo   %GR%[+]%X% Кітапханалар орнатылды.
    echo.
) else (
    call .venv\Scripts\activate.bat
)

:: ─── Ақпарат ───
echo   %G%──────────────────────────────────────────────────%X%
echo     %O%►%X% Жоба:        %B%QAZAN MP3 MUSIC DOWNLOADER%X%
echo     %O%►%X% Орта:        %GR%.venv%X%
echo     %O%►%X% Веб-панель:  %C%http://127.0.0.1:8000%X%
echo   %G%──────────────────────────────────────────────────%X%
echo.
echo   %GR%[+]%X% Жүйе іске қосылуда... %G%(Ctrl+C - тоқтату)%X%
echo.

python main.py
set "EXIT_CODE=%errorlevel%"

echo.
echo   %G%──────────────────────────────────────────────────%X%
if "%EXIT_CODE%"=="0" (
    echo     %O%[*]%X% Жүйе қалыпты тоқтатылды.
) else (
    echo     %R%[!]%X% Жүйе %EXIT_CODE% кодымен тоқтатылды.
)
echo   %G%──────────────────────────────────────────────────%X%
echo.

pause