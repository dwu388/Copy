@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  echo Usage: run_learning_loop.bat path\to\fomo_buy_sell_notifications.csv [train-before-UTC] [test-start-UTC]
  exit /b 2
)
if "%~2"=="" (
  py -3 -m learning.train_2x "%~1" --output-dir local_learning_state\net2x
) else (
  if "%~3"=="" (
    echo Provide both train-before and test-start in YYYY-MM-DD format.
    exit /b 2
  )
  py -3 -m learning.train_2x "%~1" --output-dir local_learning_state\net2x --train-before "%~2" --test-start "%~3"
)
exit /b %errorlevel%
