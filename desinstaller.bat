@echo off
rem Ready or Not - IA vocale : removes UE4SS and the mod from the game (only the files the install added).
cd /d "%~dp0"
if not exist manifeste_ue4ss.json (
  echo Le mod n'est pas installe dans le jeu.
  pause
  exit /b 0
)
.venv\Scripts\python.exe outils\installer_ue4ss.py remove manifeste_ue4ss.json && del manifeste_ue4ss.json
pause
