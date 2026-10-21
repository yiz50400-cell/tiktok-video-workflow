@echo off
setlocal
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0.."
set "WORKFLOW_PYTHON=%CD%\runtime\python\python.exe"
if not exist "%WORKFLOW_PYTHON%" set "WORKFLOW_PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "%WORKFLOW_PYTHON%" (
  echo Python runtime missing. Follow README Windows source setup.
  exit /b 1
)
"%WORKFLOW_PYTHON%" -X utf8 -m uvicorn workflow.api:create_app --factory --host 127.0.0.1 --port 8765
exit /b %errorlevel%
