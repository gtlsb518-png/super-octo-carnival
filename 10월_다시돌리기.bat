@echo off
cd /d "%~dp0"
echo Replay 2026-10-03 ~ 2026-10-30 (the 15m period) with real Binance charts:
echo   15m 5x UT10/5 (October setting)  vs  1h 3x UT15/5 (current setting)  and others
echo First run downloads charts (about 5-15 min). Result: real_chart result txt file in this folder.
python real_chart_check.py --what period
pause
