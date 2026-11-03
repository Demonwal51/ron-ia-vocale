@echo off
rem Ready or Not - IA vocale : starts the project's AI server (hidden), then listens to the player's voice.
rem Close this window (or Ctrl+C) to stop listening.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File cerveau\ollama_projet.ps1
.venv\Scripts\python.exe ia_vocale.py
pause
