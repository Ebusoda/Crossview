@echo off
rem Crossview web: starts the web app on this PC and opens it in the browser.
rem This PC can open it; with Tailscale installed and signed in, your own phone and tablet can too.
rem Close this window to stop it.
rem Data (accounts, chats, encrypted API keys) stays in the .maoweb folder next to this file and is never uploaded.
setlocal
cd /d "%~dp0"
python -X faulthandler -m maoweb --remote
echo.
echo Crossview web stopped at %date% %time% (exit code %errorlevel%). See the messages above.
pause
