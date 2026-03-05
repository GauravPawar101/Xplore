@echo off
echo Starting EzDocs...
echo.

echo [1/2] Starting Backend Server...
start "EzDocs Backend" cmd /k "cd backend && python main.py"

timeout /t 3 /nobreak > nul

echo [2/2] Starting Frontend Dev Server...
start "EzDocs Frontend" cmd /k "cd frontend && npm run dev"

echo.
echo ✅ EzDocs is starting!
echo.
echo Backend:  http://localhost:8000
echo Frontend: http://localhost:5173
echo.
echo Press any key to stop all servers...
pause > nul

taskkill /FI "WINDOWTITLE eq EzDocs Backend" /T /F
taskkill /FI "WINDOWTITLE eq EzDocs Frontend" /T /F
