@echo off
setlocal

where adb >nul 2>nul
if errorlevel 1 (
    echo ERROR: adb was not found in PATH.
    exit /b 1
)

echo Dumping current Android accessibility hierarchy...
adb shell uiautomator dump /sdcard/fomo-ui.xml
if errorlevel 1 exit /b 1

adb pull /sdcard/fomo-ui.xml "%~dp0fomo-ui.xml"
if errorlevel 1 exit /b 1

echo.
echo Saved:
echo %~dp0fomo-ui.xml
echo.
echo Search for resource-id, Buy, Sell, EditText, clickable, and content-desc.
endlocal
