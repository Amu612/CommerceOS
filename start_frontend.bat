@echo off
title Orders Agent Dashboard
cd frontend
if not exist node_modules (
    echo Installing frontend dependencies...
    npm install
)
echo Starting Orders Agent Dashboard on http://localhost:3000...
npm run dev
pause
