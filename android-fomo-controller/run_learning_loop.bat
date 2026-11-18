@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  echo Usage: run_learning_loop.bat path\to\fomo_buy_sell_notifications.csv
  exit /b 2
)
py -3 -m learning.prepare_raw_notifications "%~1" local_learning_state\policy_learning.csv
if errorlevel 1 exit /b %errorlevel%
py -3 -m learning.run_maintenance local_learning_state\policy_learning.csv --state-dir local_learning_state
exit /b %errorlevel%
