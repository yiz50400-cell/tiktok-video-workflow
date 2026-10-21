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
"%WORKFLOW_PYTHON%" -X utf8 scripts\check_windows_engine.py --require-windows
if errorlevel 1 exit /b %errorlevel%
"%WORKFLOW_PYTHON%" -X utf8 scripts\check.py --junitxml=.runtime/windows-engine-check/regression.xml
exit /b %errorlevel%
