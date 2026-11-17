@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install Python 3.9+ or run: py app.py
  pause
  exit /b 1
)
echo Starting PlantDesigner 3D ...
python app.py
pause
