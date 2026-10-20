@echo off
rem ==============================================================
rem  Refresh orbit deliverables: chase daily history to today,
rem  rebuild both climb charts (HTML) and their full-page PNGs.
rem  IMPORTANT: this file must stay PURE ASCII and use CRLF line
rem  endings. cmd.exe decodes .bat with the OEM codepage, so any
rem  non-ASCII char here gets mangled into bogus commands.
rem  Chinese guide: see the .md docs in this folder.
rem ==============================================================
setlocal EnableExtensions
chcp 65001 >nul 2>nul
set PYTHONUTF8=1
cd /d "%~dp0"

set "PYEXE="
for %%P in (
  "C:\Users\t2007\.workbuddy\binaries\python\versions\3.13.12\python.exe"
  "C:\Users\t2007\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
  "C:\Python313\python.exe"
  "C:\Python312\python.exe"
) do (
  if not defined PYEXE if exist %%P (
    %%~P -c "import sys" >nul 2>nul && set "PYEXE=%%~P"
  )
)
if not defined PYEXE (
  for %%P in (python.exe) do set "PYEXE=%%~$PATH:P"
)
if not defined PYEXE (
  echo [ERROR] python interpreter not found.
  pause
  exit /b 1
)

echo Using: %PYEXE%
"%PYEXE%" refresh_deliverables.py %*
echo.
echo Exit code: %ERRORLEVEL%
if "%~1"=="" pause
endlocal
