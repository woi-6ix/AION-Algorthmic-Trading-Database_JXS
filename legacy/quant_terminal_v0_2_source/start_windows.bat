@echo off
rem Launch in a persistent Command Prompt so errors stay visible instead of disappearing.
cd /d "%~dp0"
if errorlevel 1 (
    echo ERROR: Could not open the AION application folder.
    pause
    exit /b 1
)
cmd.exe /D /K "call run_aion_windows.bat"
