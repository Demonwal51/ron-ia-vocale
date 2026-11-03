@echo off
rem Ready or Not - IA vocale : pick your microphone and your push-to-talk key (writes config.json).
cd /d "%~dp0"
.venv\Scripts\python.exe outils\essai_micro.py
pause
