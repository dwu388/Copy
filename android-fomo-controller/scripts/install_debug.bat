@echo off
setlocal

set APK=%~dp0..\app\build\outputs\apk\debug\app-debug.apk

if not exist "%APK%" (
    echo ERROR: Debug APK was not found:
    echo %APK%
    echo Build it in Android Studio or run Gradle assembleDebug first.
    exit /b 1
)

adb install -r "%APK%"
endlocal
