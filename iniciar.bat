@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
if not exist "transcrever.pid" goto start
set /p PID=<"transcrever.pid"
tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
if not errorlevel 1 (
  echo Ja esta gravando.
  echo Log: %cd%\logs\atual.txt
  exit /b 0
)
:start
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
start "Transcricao da chamada" /MIN "%PY%" -u -m discord_stt %*
set "TRIES=0"
:waitpid
if exist "transcrever.pid" goto ready
set /a TRIES+=1
if %TRIES% GEQ 20 goto failed
timeout /t 1 /nobreak >nul
goto waitpid
:ready
set /p PID=<"transcrever.pid"
echo Interface ligada ^(pid %PID%^).
echo Abra: http://127.0.0.1:8765
echo Log: %cd%\logs\atual.txt
echo Parar: %cd%\parar.bat
exit /b 0
:failed
echo Nao consegui manter a transcricao rodando. Veja logs\console.log
exit /b 1
