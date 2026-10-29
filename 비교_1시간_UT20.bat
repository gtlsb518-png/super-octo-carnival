@echo off
cd /d "%~dp0"
echo 1h UT 10/5  vs  15m UT 20/5  (+ current 15m UT 10/5), Binance real charts since 2020-01-01
echo First run downloads charts: may take 30-60 min. Result: real_chart_result txt in this folder.
python real_chart_check.py --what two --since 2020-01-01
pause
