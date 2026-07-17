@echo off
REM stop.bat - Ask a running MTalk notifier to shut down cleanly.
REM
REM Tries three strategies, in order, each strictly graceful:
REM   1. Set the Windows named event 'MTalkNotifier_Stop' via PowerShell.
REM      The notifier's stop-signal listener is waiting on this event and
REM      will trigger an ordered LIFO shutdown as soon as it fires.
REM   2. Write a stop.request file next to config.json. The notifier's
REM      file watcher (also part of the stop-signal listener) will observe
REM      it, delete it, and trigger the same ordered shutdown.
REM   3. As a last resort, read the PID file mtalk_notifier.pid and issue
REM      taskkill /PID <n> WITHOUT /F (so the process still receives a
REM      graceful signal). We NEVER use /F here; the shutdown watchdog
REM      inside the process already guarantees hard exit after the
REM      deadline.

setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo [stop.bat] Requesting graceful shutdown of MTalk notifier...

REM ----- 1. Windows named event ---------------------------------------------
where powershell >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "try { $e = [System.Threading.EventWaitHandle]::OpenExisting('MTalkNotifier_Stop'); $e.Set(); Write-Host '[stop.bat] Named event signalled.'; exit 0 } catch { exit 1 }"
    if !ERRORLEVEL! EQU 0 (
        goto :done
    ) else (
        echo [stop.bat] Named event 'MTalkNotifier_Stop' not found ^(notifier may not be running^).
    )
)

REM ----- 2. Stop-request file -----------------------------------------------
echo stop > stop.request
if exist stop.request (
    echo [stop.bat] Wrote stop.request; the notifier will pick it up within one poll interval.
    goto :done
)

REM ----- 3. PID-file taskkill (still graceful, no /F) -----------------------
if exist mtalk_notifier.pid (
    set /p PID=<mtalk_notifier.pid
    if defined PID (
        echo [stop.bat] Sending taskkill to PID !PID! ^(graceful^).
        taskkill /PID !PID! >nul 2>&1
        goto :done
    )
)

echo [stop.bat] Could not find a running MTalk notifier to stop.
exit /b 1

:done
echo [stop.bat] Shutdown request delivered.
exit /b 0
