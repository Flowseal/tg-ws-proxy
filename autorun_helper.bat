@echo off

if not "%1"=="admin" (
    call :check_app_path
    call :check_command powershell

    echo Requesting admin rights...
    powershell -NoProfile -Command "Start-Process 'cmd.exe' -ArgumentList '/c \"\"%~f0\" admin\"' -Verb RunAs"
    exit
)

setlocal EnableDelayedExpansion
set "AUTORUN_REG_PATH=HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
set "AUTORUN_REG_VALUE=TgWsProxy"
set "EXE_PATH=%~dp0TgWsProxy.exe"

reg query "%AUTORUN_REG_PATH%" /v "%AUTORUN_REG_VALUE%" >nul 2>&1
if %errorlevel% equ 0 (
    echo TgWsProxy already in autorun
    set /p choice="Remove from autorun? (Y/N) (default: N) "
    if /i "!choice!"=="y" (
        reg delete "%AUTORUN_REG_PATH%" /v "%AUTORUN_REG_VALUE%" /f >nul
        echo Removed from autorun
    ) else (
        echo Cancelled
    )
) else (
    set /p choice="Add to autorun? (Y/N) (default: N) "
    if /i "!choice!"=="y" (
        reg add "%AUTORUN_REG_PATH%" /v "%AUTORUN_REG_VALUE%" /t REG_SZ /d "\"%EXE_PATH%\"" /f >nul
        echo Added to autorun
    ) else (
        echo Cancelled
    )
)

endlocal
exit /b 0


:check_app_path
if not exist "%EXE_PATH%" (
    echo This script and TgWsProxy must be in the same directory
    pause
    exit
)
exit /b 0

:check_command
where %1 >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] %1 not found in PATH
    echo Fix your PATH variable with instructions here https://github.com/Flowseal/zapret-discord-youtube/issues/7490
    pause
    exit /b 1
)
exit /b 0
