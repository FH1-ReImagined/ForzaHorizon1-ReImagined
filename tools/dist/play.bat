@echo off
rem Start the game with the files extracted from your own disc (see README.txt):
rem   play.bat C:\path\to\extracted\game [options]     e.g. --resolution_scale=2
rem   play.bat --offline                               no Xbox LIVE (no Forza Live server)
rem The path is remembered in game_data_root.txt. FORZA_SERVER=host[:port] names a shared
rem Forza Live server (or live_server = "host:port" in forza.toml).
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "OFFLINE=0"
set "DATA="
set "ARGS="
if exist game_data_root.txt set /p DATA=<game_data_root.txt
set "FIRST=1"
:next
if "%~1"=="" goto parsed
set "A=%~1"
if /i "!A!"=="--offline" (
  set "OFFLINE=1"
) else if "!A:~0,1!"=="-" (
  set ARGS=!ARGS! %1
) else if "!FIRST!"=="1" (
  set "DATA=!A!"
) else (
  set ARGS=!ARGS! %1
)
set "FIRST=0"
shift
goto next
:parsed
if not defined DATA (
  echo usage: play.bat C:\path\to\extracted\game [options] ^(README.txt explains the extraction^)
  exit /b 1
)
if not exist "%DATA%\default.xex" (
  echo no default.xex in %DATA%: point at the extracted game files
  exit /b 1
)
> game_data_root.txt echo %DATA%
set "DOCS=%USERPROFILE%\Documents\forza"
if not exist "%DOCS%" mkdir "%DOCS%"
if "%OFFLINE%"=="1" set "FORZA_SERVER="
if "%OFFLINE%"=="1" goto run
if defined FORZA_SERVER goto run
findstr /r /c:"^[ ]*live_server[ ]*=[ ]*\"[^\"]" forza.toml >nul 2>&1
if not errorlevel 1 goto run
where python >nul 2>&1
if errorlevel 1 (
  echo Python 3 not found: starting without a local Forza Live server ^(like --offline^)
  goto run
)
rem Local server: reuse one already listening on 21201, else start one for this PC.
powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient('127.0.0.1',21201)).Close();exit 0}catch{exit 1}"
if errorlevel 1 (
  if not exist "%DOCS%\live_server" mkdir "%DOCS%\live_server"
  start "Forza Live server" /min python server\forza_live_server.py --host 127.0.0.1 --port 21200 --http-port 21201 --data-dir "%DOCS%\live_server" --game-db "%DATA%\media\db\gamedb.slt"
  timeout /t 1 /nobreak >nul
)
set "FORZA_SERVER=127.0.0.1:21200"
:run
forza.exe --game_data_root="%DATA%" --log_file="%DOCS%\forza.log" %ARGS%
exit /b %errorlevel%
