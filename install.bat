@echo off
REM ============================================================================
REM  LaunchPad Controller - Windows Installer
REM  For Novation Launchpad Mini Mk2
REM ============================================================================

echo.
echo   ╔═══════════════════════════════════════════╗
echo   ║       LAUNCHPAD CONTROLLER INSTALLER      ║
echo   ║     Novation Launchpad Mini Mk2 Tool      ║
echo   ╚═══════════════════════════════════════════╝
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH.
    echo.
    echo Please install Python 3.8+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

echo [1/3] Installing Python dependencies...
pip install PyQt5 mido python-rtmidi sounddevice soundfile pydub numpy
if errorlevel 1 (
    echo [WARNING] Some packages may have failed. Trying with --user flag...
    pip install --user PyQt5 mido python-rtmidi sounddevice soundfile pydub numpy
)
echo.

echo [2/3] Installing LaunchPad Controller...
pip install -e .
if errorlevel 1 (
    pip install --user -e .
)
echo.

echo [3/3] Creating start script...

REM Create a convenient launcher batch file
echo @echo off > "%~dp0launch.bat"
echo python -m launchpad_ctrl >> "%~dp0launch.bat"

REM Create a desktop shortcut via PowerShell
powershell -Command ^
    "$ws = New-Object -ComObject WScript.Shell; ^
     $shortcut = $ws.CreateShortcut([System.IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'LaunchPad Controller.lnk')); ^
     $shortcut.TargetPath = 'pythonw'; ^
     $shortcut.Arguments = '-m launchpad_ctrl'; ^
     $shortcut.WorkingDirectory = '%~dp0'; ^
     $shortcut.Description = 'LaunchPad Controller for Novation Launchpad Mini Mk2'; ^
     $shortcut.Save()" 2>nul

if not errorlevel 1 (
    echo   Desktop shortcut created.
) else (
    echo   Could not create shortcut - you can run launch.bat instead.
)

echo.
echo ╔═══════════════════════════════════════════╗
echo ║         Installation Complete!             ║
echo ╚═══════════════════════════════════════════╝
echo.
echo   To run:
echo     launchpad-ctrl
echo   or:
echo     python -m launchpad_ctrl
echo   or double-click:
echo     launch.bat
echo.
echo   NOTE: For MP3 support, install ffmpeg:
echo     1. Download from https://ffmpeg.org/download.html
echo     2. Add the bin folder to your PATH
echo.
echo   Config directory: %%USERPROFILE%%\.launchpad-ctrl\
echo.
pause
