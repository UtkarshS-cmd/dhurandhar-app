@echo off
setlocal enabledelayedexpansion
title Dhurandhar Cinema

rem ============================================================
rem  Dhurandhar Cinema - one-click launcher (Windows)
rem  Checks Python, venv, deps, .env, assets, DB, then starts
rem  the FastAPI server (which also serves index.html + assets)
rem  on port 5000.
rem ============================================================

set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "VENV=%BACKEND%\.venv"
if not defined PORT set "PORT=5000"
set "STEP=0"

echo ============================================
echo    Dhurandhar Cinema - starting...
echo ============================================

rem --- 1. Locate a Python interpreter ------------------------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [ERROR] Python was not found on PATH.
    echo         Install it from https://www.python.org/downloads/
    echo         and tick "Add python.exe to PATH".
    pause
    exit /b 1
)
for /f "delims=" %%v in ('%PY% --version 2^>^&1') do echo [ok] Python: %%v
%PY% -c "import sys; raise SystemExit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python 3.10+ is required.
    pause
    exit /b 1
)

rem --- 2. Create virtual environment if missing --------------
if not exist "%VENV%\Scripts\python.exe" (
    echo [*] Creating virtual environment...
    %PY% -m venv "%VENV%"
    if errorlevel 1 (
        echo [ERROR] Failed to create the virtual environment.
        pause
        exit /b 1
    )
) else (
    echo [ok] Virtual environment exists.
)

set "VPY=%VENV%\Scripts\python.exe"

rem --- 3. Install / verify dependencies ----------------------
"%VPY%" -c "import fastapi, uvicorn, sqlalchemy, alembic, jwt, pydantic, httpx, pytest" >nul 2>nul
if errorlevel 1 (
    echo [*] Installing dependencies - first run only, please wait...
    "%VPY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
    "%VPY%" -m pip install -r "%BACKEND%\requirements.txt" --quiet --disable-pip-version-check
    if errorlevel 1 (
        echo [ERROR] pip install failed. Check your internet connection
        echo         and re-run this file.
        pause
        exit /b 1
    )
    echo [ok] Dependencies installed.
) else (
    echo [ok] Dependencies already installed.
)
"%VPY%" -c "import fastapi, uvicorn, sqlalchemy, alembic, jwt, pydantic, httpx, pytest" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Dependency verification failed even after install.
    pause
    exit /b 1
)

rem --- 4. Create .env if missing -----------------------------
if not exist "%BACKEND%\.env" (
    echo [*] Creating .env from .env.example...
    copy /y "%BACKEND%\.env.example" "%BACKEND%\.env" >nul
    echo [ok] .env created.
) else (
    echo [ok] .env exists.
)

rem --- 5. Verify frontend assets are present -----------------
rem Full asset manifest: 2 video files, 8 audio tracks, 17 gallery images,
rem 3 stylesheets, 6 JS modules. Any missing or empty (0-byte) file is
rem reported by name so the gap is obvious.
set "MISSING=0"
if not exist "%ROOT%index.html" (
    echo [ERROR] Missing: index.html
    set "MISSING=1"
)
for %%D in (css js assets backend) do (
    if not exist "%ROOT%%%D" (
        echo [ERROR] Missing folder: %%D
        set "MISSING=1"
    )
)
set "MISSING_ASSET=0"
for %%F in (
    "assets\video\hero.mp4"
    "assets\video\hero-poster.jpg"
    "assets\audio\title-track.mp3"
    "assets\audio\ez-ez.mp3"
    "assets\audio\ishq-jalakar.mp3"
    "assets\audio\lutt-le-gaya.mp3"
    "assets\audio\move-yeh-ishq-ishq.mp3"
    "assets\audio\naal-nachna.mp3"
    "assets\audio\shararat.mp3"
    "assets\audio\teri-ni-kararan.mp3"
    "assets\images\image-01.jpg"
    "assets\images\image-02.jpg"
    "assets\images\image-03.jpg"
    "assets\images\image-04.jpg"
    "assets\images\image-05.jpg"
    "assets\images\image-06.jpg"
    "assets\images\image-07.jpg"
    "assets\images\image-08.jpg"
    "assets\images\image-09.jpg"
    "assets\images\image-10.jpg"
    "assets\images\image-11.jpg"
    "assets\images\image-12.jpg"
    "assets\images\image-13.jpg"
    "assets\images\image-14.jpg"
    "assets\images\image-15.jpg"
    "assets\images\image-16.jpg"
    "assets\images\image-17.jpg"
    "css\styles.css"
    "css\booking.css"
    "css\responsive.css"
    "js\app.js"
    "js\api.js"
    "js\booking.js"
    "js\music-player.js"
    "js\navigation.js"
    "js\video.js"
    "js\animations.js"
) do (
    if not exist "%ROOT%%%~F" (
        echo [WARN] Missing asset: %%~F
        set "MISSING_ASSET=1"
    ) else (
        for %%S in ("%ROOT%%%~F") do if %%~zS==0 (
            echo [WARN] Empty asset ^(0 bytes^): %%~F
            set "MISSING_ASSET=1"
        )
    )
)
if "!MISSING_ASSET!"=="0" (
    echo [ok] All 37 frontend assets present ^(2 video, 8 audio, 17 images, 3 css, 7 js^).
) else (
    echo [WARN] Some assets are missing - hero falls back to the poster,
    echo        the music player skips missing tracks, and missing gallery
    echo        images show empty cells.
)
if "!MISSING!"=="1" (
    echo [ERROR] Required frontend files are missing - cannot start.
    pause
    exit /b 1
)

rem --- 6. Run migrations and seed ----------------------------
pushd "%BACKEND%"
echo [*] Running database migrations...
"%VPY%" -m alembic upgrade head
if errorlevel 1 (
    echo [ERROR] Database migration failed.
    popd
    pause
    exit /b 1
)
echo [ok] Migrations up to date.

echo [*] Seeding development data...
"%VPY%" seed.py
if errorlevel 1 (
    echo [ERROR] Seeding failed.
    popd
    pause
    exit /b 1
)
echo [ok] Seed data ready.

rem --- 7. Quick backend self-check ---------------------------
echo [*] Self-check: importing backend app...
"%VPY%" -c "from app.main import app; print('    routes:', len(app.routes))"
if errorlevel 1 (
    echo [ERROR] Backend failed to import - see the error above.
    popd
    pause
    exit /b 1
)
echo [*] Self-check: verifying asset references in index.html and js...
"%VPY%" "%ROOT%check_assets.py"
if errorlevel 1 (
    echo [ERROR] Asset reference check failed - see missing files above.
    popd
    pause
    exit /b 1
)

rem --- 8. Warn if the port is already in use -----------------
netstat -ano | findstr "LISTENING" | findstr ":%PORT% " >nul
if not errorlevel 1 (
    echo.
    echo [WARNING] Port %PORT% is already in use.
    echo           Another instance may be running. Stop it first
    echo           with Ctrl+C, or continue anyway to see the error.
    echo.
    set /p CONTINUE_ANYWAY="Continue anyway? (y/N): "
    if /i not "!CONTINUE_ANYWAY!"=="y" (
        echo Stopped. Free port %PORT% and re-run this file.
        pause
        popd
        exit /b 1
    )
)

rem --- 9. Start the server -----------------------------------
echo.
echo [ok] Opening http://localhost:%PORT% - press Ctrl+C to stop.
echo.
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://localhost:%PORT%"
"%VPY%" -m uvicorn app.main:app --host 0.0.0.0 --port %PORT%
set "EXITCODE=%ERRORLEVEL%"
popd
if not "%EXITCODE%"=="0" (
    echo [ERROR] Server exited with code %EXITCODE%.
    pause
    exit /b %EXITCODE%
)
endlocal
