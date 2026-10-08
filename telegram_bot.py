#!/usr/bin/env python3
"""📱 텔레그램 봇 — 실시간 알림 + 버튼으로 상황 보기·조작

프로그램 하나에 봇 하나 (BotFather 에서 /newbot 으로 만든 토큰을 1_config.py 의 TELEGRAM_BOT_TOKENS 에).
- 알림: 프로그램 화면 로그에서 진입·익절·스위칭·청산·오류 줄을 골라 보낸다 (알림 종류는 버튼으로 켜고 끔)
- 버튼: 현황 / 손익 / 코인별 상세(화면 내용 그대로) / 시작·정지·1회매매·이 코인 청산 / 전체 종료
- 주인(내 대화방)이 누른 버튼만 동작. 다른 사람이 봇을 찾아 눌러도 무시
- 추가 설치 없음 (requests 만 사용, 텔레그램 서버에 먼저 물어보는 방식이라 공유기·방화벽 설정 필요 없음)
"""
import json
import os
import queue
import re
import threading
import time
from datetime import datetime

import requests

API_BASE = 'https://api.telegram.org'

# (종류, 버튼 이름, 기본값)
ALERT_KINDS = [
    ('entry', '진입', False),
    ('tp', '익절', True),
    ('switch', '스위칭·손절', True),
    ('close', '청산·정지', True),
    ('error', '오류', True),
    ('daily', '하루 요약', True),
]
DAILY_AT = (9, 0)            # 하루 요약 보내는 시각 (이 PC 시간)
TS_RE = re.compile(r'^\[(\d{4}-\d{2}-\d{2} )?(\d{2}:\d{2}:\d{2})\] ?')


def _short(sym):
    return sym.split('/')[0]


def _px(p):
    try:
        p = float(p)
    except (TypeError, ValueError):
        return str(p)
    if p >= 100:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:.4f}"
    return f"{p:.6f}".rstrip('0').rstrip('.') if p > 0 else "0"


class TelegramBot:
    def __init__(self, app, token, program_no, folder='.', chat_id=None):
        self.app = app
        self.token = token.strip()
        self.no = program_no
        self.base = (os.environ.get('TELEGRAM_API_BASE') or API_BASE).rstrip('/')
        sfx = '' if program_no == 1 else f'_{program_no}'
        self.owner_file = os.path.join(folder, f'telegram_owner{sfx}.json')
        self.alert_file = os.path.join(folder, f'telegram_alerts{sfx}.json')
        self.s = requests.Session()
        self.owner = str(chat_id).strip() if chat_id else self._load_owner()
        self.alerts = self._load_alerts()
        self.username = None
        self._offset = None
        self._stop = False
        self._out = queue.Queue()          # 보낼 알림
        self._buf = {}                     # 심볼 → {'kind', 'lines', 't'} (알림 한 건을 몇 줄 모아서 보냄)
        self._recent = {}                  # 같은 알림 중복 방지 (롱·숏 창에 같은 줄이 두 번 찍히는 경우)
        self._skip = {}                    # 심볼 → 중복 묶음을 건너뛰는 중인 시각
        self._lock = threading.Lock()
        self._warned = set()

    # ==================== 저장 ====================
    def _load_owner(self):
        try:
            with open(self.owner_file, encoding='utf-8') as f:
                return str(json.load(f).get('chat_id') or '') or None
        except Exception:
            return None

    def _save_owner(self, chat_id):
        self.owner = str(chat_id)
        try:
            with open(self.owner_file, 'w', encoding='utf-8') as f:
                json.dump({'chat_id': self.owner}, f)
        except Exception as e:
            print(f"⚠️ [텔레그램] 주인 저장 실패: {e}")

    def _load_alerts(self):
        a = {k: d for k, _, d in ALERT_KINDS}
        try:
            with open(self.alert_file, encoding='utf-8') as f:
                a.update({k: bool(v) for k, v in json.load(f).items() if k in a})
        except Exception:
            pass
        return a

    def _save_alerts(self):
        try:
            with open(self.alert_file, 'w', encoding='utf-8') as f:
                json.dump(self.alerts, f)
        except Exception as e:
            print(f"⚠️ [텔레그램] 알림 설정 저장 실패: {e}")

    # ==================== 텔레그램 서버 ====================
    def _call(self, method, http_timeout=15, **params):
        try:
            r = self.s.post(f"{self.base}/bot{self.token}/{method}", json=params, timeout=http_timeout)
            j = r.json()
        except Exception as e:
            if method != 'getUpdates':
                print(f"⚠️ [텔레그램] {method} 실패: {e}")
            return None
        if j.get('ok'):
            return j.get('result')
        code, desc = j.get('error_code'), j.get('description', '')
        if 'message is not modified' in desc:
            return True
        key = (method, code)
        if key not in self._warned:
            self._warned.add(key)
            if code == 401:
                print("❌ [텔레그램] 토큰이 틀렸습니다 — 1_config.py 의 TELEGRAM_BOT_TOKENS 확인 (텔레그램 끔)")
                self._stop = True
            elif code == 409:
                print("⚠️ [텔레그램] 같은 봇을 다른 프로그램이 쓰고 있습니다 — 프로그램마다 다른 봇 토큰을 넣으세요")
            else:
                print(f"⚠️ [텔레그램] {method}: {desc} (코드 {code})")
        return None

    def send(self, text, buttons=None):
        if not self.owner:
            return None
        p = {'chat_id': self.owner, 'text': text[:4000], 'disable_web_page_preview': True}
        if buttons:
            p['reply_markup'] = {'inline_keyboard': buttons}
        return self._call('sendMessage', **p)

    def _edit(self, chat_id, msg_id, text, buttons):
        p = {'chat_id': chat_id, 'message_id': msg_id, 'text': text[:4000], 'disable_web_page_preview': True,
             'reply_markup': {'inline_keyboard': buttons or []}}
        if self._call('editMessageText', **p) is None:
            self.send(text, buttons)        # 오래된 메시지라 못 고치면 새로 보냄

    # ==================== 시작 ====================
    def start(self):
        me = self._call('getMe')
        if not me:
            print("⚠️ [텔레그램] 봇에 연결하지 못했습니다 (토큰·인터넷 확인). 프로그램은 그대로 돌아갑니다")
            return False
        self.username = me.get('username')
        print(f"📱 텔레그램 연결: @{self.username} [P{self.no}] — "
              + ("주인 등록됨" if self.owner else "봇에게 아무 말이나 보내면 그 대화방을 주인으로 등록합니다"))
        for fn, name in ((self._poll_loop, 'tg-poll'), (self._send_loop, 'tg-send'), (self._daily_loop, 'tg-daily')):
            threading.Thread(target=fn, daemon=True, name=name).start()
        if self.owner:
            self.send(f"🟢 [P{self.no}] 프로그램 시작 — 코인 {len(self.app.coins)}개", self._main_buttons())
        return True

    def stop(self, say_bye=True):
        if say_bye and self.owner and not self._stop:
            try:
                self.s.post(f"{self.base}/bot{self.token}/sendMessage",
                            json={'chat_id': self.owner, 'text': f"🔴 [P{self.no}] 프로그램 종료"}, timeout=3)
            except Exception:
                pass
        self._stop = True

    # ==================== 받기 ====================
    def _poll_loop(self):
        fails = 0
        while not self._stop:
            p = {'timeout': 25, 'allowed_updates': ['message', 'callback_query']}
            if self._offset is not None:
                p['offset'] = self._offset
            ups = self._call('getUpdates', http_timeout=35, **p)
            if ups is None:
                fails += 1
                time.sleep(min(30, 2 * fails))
                continue
            fails = 0
            for u in ups:
                self._offset = u['update_id'] + 1
                try:
                    if 'callback_query' in u:
                        self._on_button(u['callback_query'])
                    elif 'message' in u:
                        self._on_message(u['message'])
                except Exception as e:
                    import traceback
                    print(f"⚠️ [텔레그램] 처리 오류: {e}\n{traceback.format_exc()}")

    def _is_owner(self, chat_id):
        return self.owner is not None and str(chat_id) == self.owner

    def _on_message(self, m):
        cid = m.get('chat', {}).get('id')
        if self.owner is None and m.get('chat', {}).get('type') == 'private':
            self._save_owner(cid)
            print(f"📱 [텔레그램] 주인 등록: {m.get('from', {}).get('first_name', '')} (대화방 {cid})")
            self.send(f"✅ 이 대화방을 [P{self.no}] 봇의 주인으로 등록했습니다.\n이제 다른 사람은 이 봇을 조작할 수 없습니다.")
        if not self._is_owner(cid):
            return
        text, buttons = self._screen_main()
        self.send(text, buttons)

    def _on_button(self, q):
        cid = q.get('message', {}).get('chat', {}).get('id')
        mid = q.get('message', {}).get('message_id')
        data = q.get('data') or ''
        if not self._is_owner(cid):
            self._call('answerCallbackQuery', callback_query_id=q['id'], text='권한이 없습니다')
            return
        self._call('answerCallbackQuery', callback_query_id=q['id'])
        cmd, _, arg = data.partition(':')

        if cmd == 'm':
            self._edit(cid, mid, *self._screen_main())
        elif cmd == 's':
            self._edit(cid, mid, *self._screen_status())
        elif cmd == 'p':
            self._edit(cid, mid, *self._screen_pnl())
        elif cmd == 'c' and not arg:
            self._edit(cid, mid, *self._screen_coins())
        elif cmd == 'c':
            self._edit(cid, mid, *self._screen_coin(arg))
        elif cmd == 'n':
            if arg in self.alerts:
                self.alerts[arg] = not self.alerts[arg]
                self._save_alerts()
            self._edit(cid, mid, *self._screen_alerts())
        elif cmd in ('start', 'pause', 'oneshot', 'exit', 'close', 'closeyes'):
            self._do_coin(cmd, arg, cid, mid)
        elif cmd == 'X':
            n = len(self.app.coins)
            self._edit(cid, mid, f"⚠️ [P{self.no}] 이 프로그램 코인 {n}개를 모두 정지하고 포지션을 청산할까요?\n"
                                 f"다른 프로그램 코인은 건드리지 않습니다. 되돌릴 수 없습니다.",
                       [[{'text': '예, 내 코인 전부 청산', 'callback_data': 'Xy'}],
                        [{'text': '취소', 'callback_data': 'm'}]])
        elif cmd == 'Xy':
            self._edit(cid, mid, f"⏳ [P{self.no}] 전체 종료 중...", [])
            threading.Thread(target=self._run_stop_all, daemon=True).start()

    # ==================== 조작 ====================
    def _coin(self, short):
        for c in list(self.app.coins):
            if _short(c['symbol']) == short:
                return c
        return None

    def _run_ui(self, fn, wait=3.0):
        """화면 스레드에서 실행하고 끝날 때까지 잠깐 기다림 (화면 칸을 바꾸는 동작용)"""
        done = threading.Event()

        def run():
            try:
                fn()
            finally:
                done.set()
        try:
            self.app.root.after(0, run)
            done.wait(wait)
        except Exception:
            fn()

    def _do_coin(self, cmd, short, cid, mid):
        coin = self._coin(short)
        if coin is None:
            self.send(f"❓ {short} 코인을 찾을 수 없습니다")
            return
        if cmd == 'oneshot':
            self._run_ui(lambda: self.app.toggle_one_shot(coin))
            self._edit(cid, mid, *self._screen_coin(short))
            return
        if cmd == 'exit':
            self._run_ui(lambda: self.app.toggle_exit_mode(coin))
            time.sleep(1.0)      # 들고 있는 포지션의 TP 주문 정리/다시 걸기 기다림
            sw = coin.get('exit_mode', 'tp') == 'switch'
            self._edit(cid, mid, *self._screen_coin(short, note=(
                '🔁 청산 방식: 스위칭만 — TP 없이 반대 신호 2개에 스위칭 (TP 주문 취소)' if sw else
                '🔁 청산 방식: TP+스위칭 — TP 에서 익절 (TP 주문 다시 걸기)')))
            return
        if cmd == 'close':
            pos = self.app.api.get_position(coin['symbol'])
            if not pos:
                self._edit(cid, mid, *self._screen_coin(short, note='ℹ️ 지금 포지션이 없습니다'))
                return
            side = pos['side'].upper()
            self._edit(cid, mid, f"⚠️ {short} {side} 포지션을 시장가로 청산할까요?\n"
                                 f"봇은 멈추지 않고, 지금 {side} 신호가 끝난 뒤 다음 신호에서 다시 진입합니다.\n"
                                 f"(진입을 완전히 막으려면 ⏸️ 정지)",
                       [[{'text': f'예, {short} 청산', 'callback_data': f'closeyes:{short}'}],
                        [{'text': '아니오', 'callback_data': f'c:{short}'}]])
            return

        def work():
            try:
                if cmd == 'start':
                    msgs = [self.app.coin_start_side(coin, s) for s in ('LONG', 'SHORT')]
                    note = '▶️ 롱·숏 시작\n' + '\n'.join(m.split('\n\n', 1)[-1].replace('\n', ' ') for m in msgs)
                elif cmd == 'pause':
                    for s in ('LONG', 'SHORT'):
                        self.app.coin_pause_side(coin, s)
                    note = '⏸️ 롱·숏 새 진입 멈춤 — 들고 있는 포지션은 계속 관리 (반대 신호 스위칭·TP). 껐다 켜도 유지'
                else:   # closeyes
                    pos = self.app.api.get_position(coin['symbol'])
                    if not pos:
                        note = 'ℹ️ 포지션이 이미 없습니다'
                    else:
                        S = pos['side'].upper()
                        r = self.app.coin_force_stop_side(coin, S)
                        if '실패' in r:
                            note = f"❌ {S} 청산 실패 — 다시 누르거나 바이낸스에서 직접 청산하세요"
                        elif '멈추지 않습니다' in r:
                            note = f"🛑 {S} 청산 완료 — 봇은 그대로, {S} 신호가 끝난 뒤 다음 신호에서 진입 (막으려면 ⏸️ 정지)"
                        else:
                            note = f"🛑 {S} 청산 완료 — {S} 봇은 정지 상태 (다시 하려면 ▶️ 시작)"
                    time.sleep(1.5)
            except Exception as e:
                note = f"❌ 실패: {e}"
            self._edit(cid, mid, *self._screen_coin(short, note=note))
        self._edit(cid, mid, f"⏳ {short} 처리 중...", [])
        threading.Thread(target=work, daemon=True).start()

    def _run_stop_all(self):
        try:
            msg = self.app.stop_all_run()
        except Exception as e:
            msg = f"❌ 전체 종료 실패: {e}"
        self.send(f"[P{self.no}] {msg}", self._main_buttons())

    # ==================== 화면 ====================
    def _main_buttons(self):
        return [[{'text': '📊 현황', 'callback_data': 's'}, {'text': '💰 손익', 'callback_data': 'p'}],
                [{'text': '🪙 코인별', 'callback_data': 'c'}, {'text': '⏹️ 전체 종료', 'callback_data': 'X'}],
                [{'text': '🔔 알림 설정', 'callback_data': 'n'}]]

    def _back(self, to='m'):
        return [{'text': '🔄 새로고침', 'callback_data': to}, {'text': '◀️ 메뉴', 'callback_data': 'm'}]

    def _positions(self):
        """심볼 → 포지션 (한 번 조회로 전부)"""
        out = {}
        for c in list(self.app.coins):
            try:
                p = self.app.api.get_position(c['symbol'])
            except Exception:
                p = None
            out[c['symbol']] = p
        return out

    def _totals(self):
        coins = list(self.app.coins)
        st = [c.get('stats') or {} for c in coins]
        tot = sum(float(s.get('total_pnl', 0) or 0) for s in st)
        fee = sum(float(s.get('total_fee', 0) or 0) for s in st)
        fund = sum(float(s.get('funding_total', 0) or 0) for s in st)
        n = sum(int(s.get('long_count', 0) or 0) + int(s.get('short_count', 0) or 0) for s in st)
        win = sum(int(s.get('long_win', 0) or 0) + int(s.get('short_win', 0) or 0) for s in st)
        today = 0.0
        S = getattr(self.app, '_stats_state', None) or {}
        t0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000
        for sym, s in (S.get('syms') or {}).items():
            today += sum(t['net'] for t in s.get('trips', []) if (t.get('close_t') or 0) >= t0)
        return dict(tot=tot, fee=fee, fund=fund, n=n, win=win, today=today)

    def _balance(self):
        try:
            return float(self.app.api.get_balance())
        except Exception:
            return 0.0

    def _state_icon(self, c, p):
        if c.get('halted_reason') and not c.get('long_active') and not c.get('short_active'):
            return '🛑'
        if p:
            return '🟢' if p['side'] == 'long' else '🔴'
        if c.get('liq_block'):
            return '⏸️'
        if not c.get('long_active') and not c.get('short_active'):
            return '⚫'
        return '⚪'

    def _screen_main(self):
        t, pos = self._totals(), self._positions()
        mode = '테스트넷' if getattr(self.app.api, 'testnet', True) else '🔴 메인넷'
        held = sum(1 for p in pos.values() if p)
        text = (f"🤖 바이낸스봇 [P{self.no}] · {mode}\n"
                f"💰 잔고 ${self._balance():,.2f}\n"
                f"📈 오늘 {t['today']:+.2f} · 누적 순수익 {t['tot']:+.2f}\n"
                f"📍 보유 {held}개 / 코인 {len(pos)}개\n"
                f"🕒 {datetime.now():%m-%d %H:%M:%S}")
        return text, self._main_buttons()

    def _screen_status(self):
        pos = self._positions()
        lines = [f"📊 [P{self.no}] 현황 ({datetime.now():%H:%M:%S})"]
        for c in list(self.app.coins):
            p = pos.get(c['symbol'])
            s = f"{self._state_icon(c, p)} {_short(c['symbol']):5s}"
            if p:
                s += f" {p['side'].upper():5s} ROI {p.get('roi_pct', 0):+6.2f}% (${p.get('pnl', 0):+.2f})"
            elif c.get('halted_reason') and not c.get('long_active') and not c.get('short_active'):
                s += f" 정지 — {c['halted_reason']}"
            elif c.get('liq_block'):
                s += f" 청산 후 대기 ({c['liq_block'].upper()} 신호 끝나면 진입)"
            elif not c.get('long_active') and not c.get('short_active'):
                s += " 꺼짐"
            else:
                s += " 포지션 없음 (신호 대기)"
            if c.get('one_shot'):
                s += ' 🎯'
            if c.get('exit_mode', 'tp') == 'switch':
                s += ' 🔁'
            lines.append(s)
        lines.append("\n🟢롱 🔴숏 ⚪대기 ⏸️청산후신호대기 🛑정지 ⚫꺼짐 🎯1회매매 🔁스위칭만")
        return '\n'.join(lines), [self._back('s')]

    def _screen_pnl(self):
        t = self._totals()
        since = ''
        S = getattr(self.app, '_stats_state', None) or {}
        if S.get('start_ms'):
            since = datetime.fromtimestamp(S['start_ms'] / 1000).strftime('%Y-%m-%d %H:%M')
        rate = t['win'] / t['n'] * 100 if t['n'] else 0
        text = (f"💰 [P{self.no}] 손익 (바이낸스 기록 기준{', ' + since + '부터' if since else ''})\n"
                f"순수익 {t['tot']:+.2f} USDT\n"
                f"수수료 −{t['fee']:.2f} · 펀딩 {t['fund']:+.2f}\n"
                f"거래 {t['n']}회 · 익절 {t['win']}회 ({rate:.0f}%)\n"
                f"오늘 실현 {t['today']:+.2f}\n"
                f"잔고 ${self._balance():,.2f}")
        return text, [self._back('p')]

    def _screen_coins(self):
        pos = self._positions()
        btns, row = [], []
        for c in list(self.app.coins):
            p = pos.get(c['symbol'])
            label = f"{self._state_icon(c, p)} {_short(c['symbol'])}"
            if p:
                label += f" {p.get('roi_pct', 0):+.1f}%"
            if c.get('one_shot'):
                label += '🎯'
            if c.get('exit_mode', 'tp') == 'switch':
                label += '🔁'
            row.append({'text': label, 'callback_data': f"c:{_short(c['symbol'])}"})
            if len(row) == 2:
                btns.append(row); row = []
        if row:
            btns.append(row)
        btns.append([{'text': '◀️ 메뉴', 'callback_data': 'm'}])
        return f"🪙 [P{self.no}] 코인을 고르세요", btns

    def _bot_cache(self, c):
        bots = getattr(self.app, 'bots', {}).get(id(c), {})
        for k in ('long', 'short'):
            b = bots.get(k)
            if b is not None and getattr(b, '_cached_signals', None):
                return b
        return None

    def _screen_coin(self, short, note=None):
        c = self._coin(short)
        if c is None:
            return f"❓ {short} 없음", [[{'text': '◀️ 뒤로', 'callback_data': 'c'}]]
        sym = c['symbol']
        L = []
        if note:
            L.append(note)
            L.append('')
        L.append(f"🪙 {sym} [P{self.no}] · {c.get('timeframe', '1h')} · {c.get('leverage')}배 · 진입금 {c.get('amount')}")
        L.append('━━━━━━━━━━━━━━━━')
        try:
            p = self.app.api.get_position(sym)
        except Exception:
            p = None
        if p:
            k = p['side']
            roi = c.get('roi') or {}
            L.append(f"📍 포지션: {'🟢' if k == 'long' else '🔴'} {k.upper()} {p.get('amount')}개")
            L.append(f"   진입 ${_px(p['entry_price'])} → 현재 ${_px(p.get('mark_price'))}")
            L.append(f"   ROI {p.get('roi_pct', 0):+.2f}% (${p.get('pnl', 0):+.2f}) · "
                     f"최고 {float(roi.get(f'{k}_max') or 0):+.1f}% / 최저 {float(roi.get(f'{k}_min') or 0):+.1f}%")
            tp = c.get(f'entry_tp_{k}')
            if isinstance(tp, (tuple, list)):
                tp = tp[0] if tp else None
            if c.get('exit_mode', 'tp') == 'switch':
                L.append("   🔁 스위칭만 — 목표 TP 없음, 반대 신호 2개(봉 마감)까지 보유")
            elif tp:
                tp = float(tp)
                tgt = p['entry_price'] * (1 + tp / 100) if k == 'long' else p['entry_price'] * (1 - tp / 100)
                L.append(f"   목표 TP ${_px(tgt)} (가격 {tp}% · ROI {tp * float(p.get('leverage') or c.get('leverage') or 1):.2f}%)"
                         f" · 거래소 TP 주문 {'✅' if c.get(f'tp_algo_{k}') else '❔'}")
        else:
            L.append("📍 포지션: 없음")
        b = self._bot_cache(c)
        sig = getattr(b, '_cached_signals', None) if b else None
        sw = getattr(b, '_cached_switch_signals', None) if b else None
        dyn = getattr(b, '_cached_dynamic_tp', None) if b else None
        if sig:
            ut = '🟢롱' if sig.get('ut_position_long') else '🔴숏' if sig.get('ut_position_short') else '⚪'
            ema = '🟢골든' if sig.get('ema_long') else '🔴데드' if sig.get('ema_short') else '⚪'
            line = f"📡 신호: UT {ut}(봉마감) · EMA{c.get('ema_fast', 34)}/{c.get('ema_slow', 55)} {ema}(실시간)"
            if isinstance(dyn, tuple) and len(dyn) == 3:
                line += f" · ADX {float(dyn[1]):.1f} {dyn[2]} → TP {dyn[0]}%"
            L.append(line)
            if p and sw:
                opp = 'short' if p['side'] == 'long' else 'long'
                L.append(f"   스위칭 조건(반대 2개, 봉마감): UT {'✅' if sw.get(f'ut_position_{opp}') else '❌'}"
                         f" EMA {'✅' if sw.get(f'ema_{opp}') else '❌'}")
        else:
            L.append("📡 신호: 봇이 꺼져 있어 계산 안 함")
        st = []
        st.append(f"롱 {'ON' if c.get('long_active') else 'OFF'}")
        st.append(f"숏 {'ON' if c.get('short_active') else 'OFF'}")
        st.append(f"🎯1회매매 {'ON' if c.get('one_shot') else 'OFF'}")
        st.append(f"🔁청산 {'스위칭만' if c.get('exit_mode', 'tp') == 'switch' else 'TP+스위칭'}")
        if c.get('liq_block'):
            st.append(f"⏸️ 청산 후 다음 신호 대기({c['liq_block'].upper()})")
        if c.get('halted_reason') and not c.get('long_active') and not c.get('short_active'):
            st.append(f"🛑 정지: {c['halted_reason']}")
        L.append('⚙️ ' + ' · '.join(st))
        s = c.get('stats') or {}
        n = int(s.get('long_count', 0) or 0) + int(s.get('short_count', 0) or 0)
        w = int(s.get('long_win', 0) or 0) + int(s.get('short_win', 0) or 0)
        L.append(f"📊 통계: {n}회 (익절 {w} / 손절 {n - w}) · 순수익 {float(s.get('total_pnl', 0) or 0):+.2f}"
                 f" · 수수료 −{float(s.get('total_fee', 0) or 0):.2f} · 펀딩 {float(s.get('funding_total', 0) or 0):+.2f}")
        logs = []
        for side in ('long', 'short'):
            for ln in (c.get(f'{side}_logs') or [])[-30:]:
                m = TS_RE.match(ln)
                logs.append(((m.group(2) if m else ''), side, TS_RE.sub('', ln)))
        logs = [x for x in logs if x[2].strip() and not x[2].startswith('=')]
        logs.sort(key=lambda x: x[0])
        if logs:
            L.append('📝 최근 로그')
            seen = set()
            for t, side, msg in logs[-14:]:
                key = (t, msg)
                if key in seen:
                    continue
                seen.add(key)
                L.append(f"{t[:5]} {msg.strip()[:90]}")
        text = '\n'.join(L)
        chart = f"https://www.tradingview.com/chart/?symbol=BINANCE:{sym.replace('/', '')}.P"
        btns = [[{'text': '🔄 새로고침', 'callback_data': f'c:{short}'}, {'text': '📈 차트', 'url': chart}],
                [{'text': '▶️ 시작', 'callback_data': f'start:{short}'}, {'text': '⏸️ 정지', 'callback_data': f'pause:{short}'}],
                [{'text': f"🎯 1회매매: {'ON' if c.get('one_shot') else 'OFF'}", 'callback_data': f'oneshot:{short}'},
                 {'text': f"🔁 청산: {'스위칭만' if c.get('exit_mode', 'tp') == 'switch' else 'TP+스위칭'}",
                  'callback_data': f'exit:{short}'}],
                [{'text': '🛑 이 코인 청산', 'callback_data': f'close:{short}'}],
                [{'text': '◀️ 코인 목록', 'callback_data': 'c'}, {'text': '🏠 메뉴', 'callback_data': 'm'}]]
        return text, btns

    def _screen_alerts(self):
        btns, row = [], []
        for k, name, _ in ALERT_KINDS:
            row.append({'text': f"{name} {'ON' if self.alerts.get(k) else 'OFF'}", 'callback_data': f'n:{k}'})
            if len(row) == 2:
                btns.append(row); row = []
        if row:
            btns.append(row)
        btns.append([{'text': '◀️ 메뉴', 'callback_data': 'm'}])
        return f"🔔 [P{self.no}] 알림 설정 — 눌러서 켜고 끔 (하루 요약은 매일 {DAILY_AT[0]:02d}:{DAILY_AT[1]:02d})", btns

    # ==================== 알림 ====================
    @staticmethod
    def classify(msg):
        m = msg.strip()
        if m.startswith('✅') and '진입!' in m:
            return 'entry'
        if '익절!' in m:
            return 'tp'
        if '스위칭!' in m or '손절 (반대 신호' in m:
            return 'switch'
        if ('강제청산 감지' in m or '수동청산 감지' in m or '봇 정지 —' in m or m.startswith('⏸️ 강제청산 후')
                or m.startswith('⏸️ 직접 청산') or m.startswith('⏸️ 청산 후')
                or m.startswith('🔓 청산된') or '강제 청산!' in m):
            return 'close'
        if m.startswith('❌'):
            return 'error'
        return None

    def on_log(self, coin, side, message):
        """App.add_log 에서 부른다 — 화면 로그 한 줄. 알림 줄이면 뒤따르는 설명 줄(들여쓴 줄)과 묶어서 보낸다."""
        if not self.owner or self._stop:
            return
        sym = coin.get('symbol', '?')
        m = message.strip()
        kind = self.classify(message)
        now = time.time()
        with self._lock:
            if kind:
                key = (sym, m)
                if now - self._recent.get(key, 0) < 5:     # 롱·숏 창에 같은 줄이 또 찍힘 → 그 묶음은 건너뜀
                    self._skip[sym] = now
                    return
                self._recent[key] = now
                self._skip.pop(sym, None)
                old = self._buf.pop(sym, None)
                if old:
                    self._out.put(old)
                self._buf[sym] = {'kind': kind, 'lines': [m], 't': now, 'sym': sym}
            elif message.startswith('   ') and m:
                if now - self._skip.get(sym, 0) < 3:
                    return
                b = self._buf.get(sym)
                if b and now - b['t'] < 3 and len(b['lines']) < 9:
                    b['lines'].append(m)

    def _send_loop(self):
        while not self._stop:
            time.sleep(0.5)
            now = time.time()
            with self._lock:
                for sym in [s_ for s_, b in self._buf.items() if now - b['t'] >= 1.5]:
                    self._out.put(self._buf.pop(sym))
                if len(self._recent) > 500:
                    self._recent = {k: t for k, t in self._recent.items() if now - t < 10}
            items = []
            while not self._out.empty():
                items.append(self._out.get())
            items = [b for b in items if self.alerts.get(b['kind'], True)]
            if not items:
                continue
            if len(items) > 6:     # 한꺼번에 많으면 한 메시지로
                self.send(f"[P{self.no}] 알림 {len(items)}건\n" + '\n'.join(
                    f"{_short(b['sym'])} {b['lines'][0]}" for b in items))
                continue
            for b in items:
                self.send(f"[P{self.no}] {_short(b['sym'])}\n" + '\n'.join(b['lines']),
                          [[{'text': f"🪙 {_short(b['sym'])} 보기", 'callback_data': f"c:{_short(b['sym'])}"}]])
                time.sleep(0.4)

    def _daily_loop(self):
        last = None
        while not self._stop:
            time.sleep(20)
            now = datetime.now()
            if (now.hour, now.minute) == DAILY_AT and last != now.date():
                last = now.date()
                if self.alerts.get('daily') and self.owner:
                    text, _ = self._screen_pnl()
                    self.send("🗓️ 하루 요약\n" + text, self._main_buttons())
