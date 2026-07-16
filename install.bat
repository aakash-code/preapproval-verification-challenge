@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==============================================
echo  Pre-Approval Verification Tool - Setup
echo ==============================================
echo This will set up everything needed to run the tool on this computer.
echo It does not need administrator access, and it's safe to run again
echo later if anything changes.
echo.

rem ---- 1. Find a suitable Python (3.10+) -----------------------------------
echo Step 1/6: Looking for Python 3.10 or newer
set "PYTHON_CMD="

for %%V in (3.13 3.12 3.11 3.10) do (
    if not defined PYTHON_CMD (
        py -%%V -c "import sys" >nul 2>&1
        if not errorlevel 1 set "PYTHON_CMD=py -%%V"
    )
)
if not defined PYTHON_CMD (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=py -3"
)
if not defined PYTHON_CMD (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    echo.
    echo [X] Setup could not finish.
    echo Python 3.10 or newer wasn't found on this computer.
    echo.
    echo   Download it from https://www.python.org/downloads/
    echo   During install, check the box that says "Add python.exe to PATH".
    echo.
    echo   Once Python is installed, double-click this file again.
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%V in ('%PYTHON_CMD% -c "import sys; print(sys.version.split()[0])"') do set "PY_VERSION=%%V"
echo   [OK] Using "%PYTHON_CMD%" (Python %PY_VERSION%)
echo.

rem ---- 2. Create the virtual environment -----------------------------------
echo Step 2/6: Setting up an isolated Python environment (.venv)
if exist ".venv\Scripts\python.exe" (
    echo   [OK] Already set up - skipping.
) else (
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 (
        echo   [X] Could not create .venv. See the message above for details.
        pause
        exit /b 1
    )
    echo   [OK] Created .venv
)
echo.

set "VENV_PY=.venv\Scripts\python.exe"
set "VENV_PIP=.venv\Scripts\pip.exe"

rem ---- 3. Install Python dependencies --------------------------------------
echo Step 3/6: Installing required packages (this can take a minute or two)
"%VENV_PIP%" install --quiet --upgrade pip
if errorlevel 1 (
    echo   [X] Could not update pip. Check your internet connection and try again.
    pause
    exit /b 1
)
"%VENV_PIP%" install --quiet -r requirements.txt
if errorlevel 1 (
    echo   [X] Could not install the required packages. Check your internet connection and try again.
    pause
    exit /b 1
)
echo   [OK] Packages installed
echo.

rem ---- 4. Install the browser used for website checks ----------------------
echo Step 4/6: Installing the browser used to check provider websites
".venv\Scripts\playwright.exe" install chromium
if errorlevel 1 (
    echo   [X] Could not install the Chromium browser used for website checks.
    echo   Check your internet connection and try running this script again.
    pause
    exit /b 1
)
echo   [OK] Browser installed
echo.

rem ---- 5. Set up local settings (.env) -------------------------------------
echo Step 5/6: Setting up local settings
if exist ".env" (
    echo   [OK] A .env file already exists - leaving it as-is.
) else (
    copy /y ".env.example" ".env" >nul
    echo   [OK] Created .env from the template.
    echo   [!] The tool works fully without an API key (Automatic engine).
    echo   [!] To unlock the AI-assisted engine and free-form chat later, open the
    echo   [!] new .env file and set ANTHROPIC_API_KEY=... - see console.anthropic.com
)
echo.

rem ---- 6. Verify the install actually works --------------------------------
echo Step 6/6: Verifying the install
"%VENV_PY%" -c "import preapproval, fastapi, playwright, pdfplumber"
if errorlevel 1 (
    echo   [X] The install finished but something isn't working correctly.
    echo   Try deleting the .venv folder and running this script again.
    pause
    exit /b 1
)
echo   [OK] Everything looks good.
echo.

echo ==============================================
echo  Setup complete!
echo ==============================================
echo.
echo To start the app, run:
echo   .venv\Scripts\python.exe -m preapproval serve
echo Then open http://127.0.0.1:8000 in your browser.
echo.

set /p START="Start the app now? [Y/n] "
if /i "%START%"=="n" goto :end

echo.
echo Starting the app - open http://127.0.0.1:8000 in your browser.
echo Press Ctrl+C in this window to stop it later.
echo.
"%VENV_PY%" -m preapproval serve

:end
echo.
pause
