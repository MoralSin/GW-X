@echo off
rem Builds GW-X.exe from GW-X.py. Run this on your Windows PC (double-click it).
rem Needs Python 3 from python.org with "Add python.exe to PATH" ticked during install.
setlocal
cd /d "%~dp0"

set PY=python
where py >nul 2>nul && set PY=py -3

%PY% --version >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install Python 3 from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" in the installer, then run this file again.
    pause
    exit /b 1
)

echo Installing the build tool and the tray icon add-ons, this can take a minute...
%PY% -m pip install --upgrade pyinstaller pystray pillow
if errorlevel 1 (
    echo Could not install the build tools. Check your internet connection and try again.
    pause
    exit /b 1
)

echo Building GW-X.exe ...
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed --hidden-import pystray._win32 --name GW-X GW-X.py
if errorlevel 1 (
    echo The build failed. Scroll up to see why.
    pause
    exit /b 1
)

echo.
echo Done. Your program is: %~dp0dist\GW-X.exe
echo Copy GW-X.exe anywhere you like. It saves its settings in gw1_preview.json next to itself.
pause
