@echo off
rem Ready or Not - IA vocale : installs everything (Python packages, AI model, voices, the mod in the game).
rem Needs Python 3.12 (python.org) and Ollama (ollama.com). Safe to run again: done steps are skipped.
cd /d "%~dp0"
set PY=
py -3.12 --version >nul 2>&1 && set PY=py -3.12
if not defined PY python --version 2>nul | findstr /b /c:"Python 3.12" >nul && set PY=python
if not defined PY (
  echo Python 3.12 est introuvable. Installe-le depuis https://www.python.org/downloads/ puis relance.
  pause
  exit /b 1
)
%PY% outils\installer.py %*
pause
