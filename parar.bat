@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist "transcrever.pid" (
  echo Nao havia transcricao rodando.
  exit /b 0
)
set /p PID=<"transcrever.pid"
echo stop> "parar.pedido"
for /l %%I in (1,1,20) do (
  tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
  if errorlevel 1 goto stopped
  timeout /t 1 /nobreak >nul
)
taskkill /F /PID %PID% >nul 2>&1
:stopped
del /f /q "transcrever.pid" "parar.pedido" >nul 2>&1
echo Transcricao parada.
exit /b 0
