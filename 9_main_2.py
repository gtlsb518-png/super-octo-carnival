#!/usr/bin/env python3
"""
프로그램 #2 실행 파일 (코인 11 ~ 20번)

더블클릭하거나:  python 9_main_2.py

- 9_main.py(프로그램 #1)와 같은 폴더에서 동시에 켜도 됩니다
- API 키는 1_config.py 에 있는 것을 같이 씁니다 (같은 계정)
- 저장 파일은 따로 씁니다:
    trade_history_2.xlsx / bot_stats_2.json / bot_running_2.json / 오류기록_2.txt
"""
import os
import runpy

os.environ['BOT_PROGRAM'] = '2'
runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '9_main.py'),
               run_name='__main__')
