@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  echo Usage: run_learning_loop.bat path\to\copy_learning_opportunities.csv
  exit /b 2
)
py -3 -m learning.run_maintenance "%~1" --state-dir local_learning_state
exit /b %errorlevel%
