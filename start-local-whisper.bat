@echo off
setlocal

REM Launch Local Whisper tray app from this folder.
REM You can create a shortcut to this .bat and pin it to Start/Taskbar.

cd /d "%~dp0"

REM Minimize the console window while launching.
start "" /min cmd /c "npm run dev"

endlocal
