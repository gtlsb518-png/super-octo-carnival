@echo off
cd /d "%~dp0"
echo 1h 3x UT15/5 (current)  vs  + EMA200 trend filter (1h EMA200 / daily EMA200, hold or close), Binance real charts since 2020-01-01
echo First run downloads charts: may take 30-60 min. Result is saved as a txt file in this folder.
python real_chart_check.py --what grid --since 2020-01-01 --rows "1시간 3배 UT15/5"
pause
