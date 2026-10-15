@echo off
echo Starting LinuxTV Flash Tool...
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 "%~dp0linuxtv-flash-tool.py"
) else (
    python "%~dp0linuxtv-flash-tool.py"
)
if errorlevel 1 (
    echo.
    echo Error: Python is required.
    echo Please install Python from https://www.python.org/downloads/
    pause
)
