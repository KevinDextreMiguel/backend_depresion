# PowerShell script to easily set up and run the FastAPI Backend locally.

Write-Host "=======================================================================" -ForegroundColor Cyan
Write-Host "   Salud Mental UPC - FastAPI Backend Runner                           " -ForegroundColor Cyan
Write-Host "=======================================================================" -ForegroundColor Cyan

# 1. Check for Virtual Environment
if (-not (Test-Path ".venv")) {
    Write-Host "[-] Virtual Environment (.venv) not found. Creating..." -ForegroundColor Yellow
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[x] Failed to create virtual environment. Ensure Python is installed." -ForegroundColor Red
        Exit
    }
    Write-Host "[+] Virtual Environment created successfully." -ForegroundColor Green
}

# 2. Activate and Install Requirements
Write-Host "[*] Activating virtual environment..." -ForegroundColor Cyan
& ".\.venv\Scripts\Activate.ps1"

Write-Host "[*] Installing/Verifying Python dependencies..." -ForegroundColor Cyan
pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "[x] Failed to install dependencies. Check your internet connection or requirements.txt" -ForegroundColor Red
    Exit
}
Write-Host "[+] Dependencies verified and ready." -ForegroundColor Green

# 3. Check for .env file
if (-not (Test-Path ".env")) {
    Write-Host "[-] .env file not found. Creating from .env.example..." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
    Write-Host "[!] Created .env. Please configure your database connection string and Supabase credentials inside it!" -ForegroundColor Magenta
}

# 4. Launch FastAPI Server
Write-Host "[*] Starting FastAPI Server with live reload on http://localhost:8000..." -ForegroundColor Green
Write-Host "[*] Interactive APIs will be available at http://localhost:8000/docs" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop the server.`n" -ForegroundColor Yellow

uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
