@echo off
echo ========================================
echo EzDocs - Ollama Setup Script
echo ========================================
echo.

echo [1/3] Checking if Ollama is installed...
where ollama >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: Ollama is not installed!
    echo.
    echo Please install Ollama first:
    echo   1. Visit: https://ollama.ai/download
    echo   2. Download and run the installer
    echo   3. Run this script again
    echo.
    pause
    exit /b 1
)
echo ✓ Ollama is installed
echo.

echo [2/3] Checking if model is downloaded...
ollama list | findstr "deepseek-coder:6.7b-instruct" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo ✓ Model already downloaded
    echo.
    goto :test_model
)

echo Model not found. Downloading deepseek-coder:6.7b-instruct...
echo This will download ~3.8GB. Please wait...
echo.
ollama pull deepseek-coder:6.7b-instruct
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: Failed to download model
    echo Please check your internet connection and try again
    pause
    exit /b 1
)
echo ✓ Model downloaded successfully
echo.

:test_model
echo [3/3] Testing model...
echo Running quick test...
echo.
ollama run deepseek-coder:6.7b-instruct "What is a function in Python? Answer in one sentence." --verbose
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: Model test failed
    echo Please check Ollama logs
    pause
    exit /b 1
)
echo.
echo ✓ Model test successful
echo.

echo ========================================
echo ✅ Setup Complete!
echo ========================================
echo.
echo The AI Narrator is ready to use!
echo.
echo Next steps:
echo   1. Make sure backend is running: python backend/main.py
echo   2. Make sure frontend is running: npm run dev (in frontend folder)
echo   3. Open http://localhost:5175
echo   4. Run analysis on a codebase
echo   5. Click "START TOUR" button
echo.
echo Model: deepseek-coder:6.7b-instruct
echo Size: ~3.8GB
echo Optimized for: Code understanding and explanation
echo.
pause
