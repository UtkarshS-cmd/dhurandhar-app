@echo off
rem E2E webServer launcher (invoked by playwright.config.cjs).
echo [e2e-server] arg0=%0 dp0=%~dp0
setlocal
echo [e2e-server] cwd=%CD%
cd /d "%~dp0..\backend"
echo [e2e-server] backend=%CD%
if not defined E2E_PORT set "E2E_PORT=5077"
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port %E2E_PORT%
