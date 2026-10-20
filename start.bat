@echo off
rem ==============================================================
rem  Constellation Orbit Viewer - launcher
rem  IMPORTANT: this file must stay PURE ASCII and use CRLF line
rem  endings. cmd.exe decodes .bat with the OEM codepage, so any
rem  non-ASCII char here gets mangled into bogus commands.
rem  Chinese user guide: the .md file in this folder.
rem ==============================================================
setlocal EnableExtensions
chcp 65001 >nul 2>nul
set PYTHONUTF8=1
cd /d "%~dp0"

set "PYEXE="

rem --- 1) known absolute locations on this machine -------------------
for %%P in (
  "C:\Users\t2007\.workbuddy\binaries\python\versions\3.13.12\python.exe"
  "C:\Users\t2007\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
  "C:\Python313\python.exe"
  "C:\Python312\python.exe"
  "C:\Python311\python.exe"
) do (
  if not defined PYEXE if exist %%P (
    %%~P -c "import sys" >nul 2>nul && set "PYEXE=%%~P"
  )
)

rem --- 2) python on PATH --------------------------------------------
if not defined PYEXE (
  for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PYEXE (
      %%P -c "import sys" >nul 2>nul && set "PYEXE=%%P"
    )
  )
)

rem --- 3) py launcher as the very last resort ----------------------
if not defined PYEXE (
  for /f "delims=" %%P in ('where py 2^>nul') do (
    if not defined PYEXE (
      %%P -3 -c "import sys" >nul 2>nul && set "PYEXE=%%P -3"
    )
  )
)

if not defined PYEXE (
  echo.
  echo   [ERROR] Python 3.9+ was not found on this computer.
  echo.
  echo   Install it from https://www.python.org/downloads/
  echo   and tick "Add python.exe to PATH" during setup.
  echo.
  pause
  exit /b 1
)

echo.
echo   Constellation Orbit Viewer
echo   ---------------------------------------------------------
echo   Python : %PYEXE%
echo   Folder : %CD%
echo.
echo   A browser tab should open by itself in a few seconds.
echo   If it does not, the address is printed below - copy it
echo   into your browser. It is also copied to the clipboard.
echo.
echo   PHONE / TABLET: on the same Wi-Fi, open the LAN address
echo   printed below (also shown in the page: right panel ->
echo   data status -> phone access). Add --no-lan below to keep
echo   it local-only.
echo.
echo   KEEP THIS WINDOW OPEN. Closing it stops the program.
echo.

%PYEXE% server.py --port 8770 --lan

echo.
echo   The server has stopped.
pause
