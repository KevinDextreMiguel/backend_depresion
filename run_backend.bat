@echo off
title Salud Mental UPC - FastAPI Backend Runner
echo =======================================================================
echo    Salud Mental UPC - FastAPI Backend Runner
echo =======================================================================

:: 1. Check for Virtual Environment
if not exist ".venv" (
    echo [-] Virtual Environment ^(.venv^) not found. Creating...
    python -m venv .venv
    if errorlevel 1 (
        echo [x] Failed to create virtual environment. Ensure Python is installed.
        pause
        exit /b 1
    )
    echo [+] Virtual Environment created successfully.
)

:: 2. Activate and Install Requirements
echo [*] Activating virtual environment...
call .venv\Scripts\activate.bat

echo [*] Installing/Verifying Python dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo [x] Failed to install dependencies. Check your internet connection or requirements.txt
    pause
    exit /b 1
)
echo [+] Dependencies verified and ready.

:: 3. Check for .env file
if not exist ".env" (
    echo [-] .env file not found. Creating from .env.example...
    copy .env.example .env
    echo [!] Created .env. Please configure your database connection string and Supabase credentials inside it!
)

:: 4. Launch FastAPI Server
echo [*] Starting FastAPI Server with live reload on http://localhost:8000...
echo [*] Interactive APIs will be available at http://localhost:8000/docs
echo Press Ctrl+C to stop the server.
echo.

uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
pause
