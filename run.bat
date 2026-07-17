@echo off
REM Convenience launcher for the MTalk notifier.
REM   run.bat            - starts silently via pythonw.exe (no console)
REM   run.bat --console  - starts in a normal console (for debugging)

setlocal
cd /d "%~dp0"

if /I "%~1"=="--console" (
    python -m src.main
    exit /b %ERRORLEVEL%
)

start "" pythonw.exe run_hidden.pyw
exit /b 0
