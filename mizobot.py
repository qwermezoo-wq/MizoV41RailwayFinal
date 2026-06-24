import os, time, requests, json, hmac, hashlib, base64, threading, traceback
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

API_KEY = "934103ed-3f3a-41a4-bea4-33d5f217cf40"
SECRET_KEY = "94A34392221477E1756F38C96A788574"
PASSPHRASE = "Mizo@2026"
BASE_URL = "https://www.okx.com"

TG_TOKEN = "8887593469:AAFKDCeleWxHuBC4p6q-vJQMTJ5V1ff0Lts"
TG_CHAT  = "5230956729"

SYMBOLS = [
    "BTC-USDT-SWAP","ETH-USDT-SWAP","SOL-USDT-SWAP",
    "ADA-USDT-SWAP","AVAX-USDT-SWAP","RUNE-USDT-SWAP",
    "TRX-USDT-SWAP"
]

CAPITAL          = 5000.0
FIXED_RISK       = 100.0
STOP_MULT        = 2.0
TGT_MULT         = 4.0
MAX_OPEN         = 4
MAX_DAILY_TRADES = 4
ADX_MIN          = 20
LOOKBACK         = 20
EMA_TREND_PERIOD = 50
USE_TREND_FILTER = True
MAX_DAILY_LOSS   = 250.0
MAX_TOTAL_LOSS   = 500.0
MIN_EQUITY       = CAPITAL - MAX_TOTAL_LOSS

usdt = CAPITAL
positions = []
TOTAL_TRADES = 0
TOTAL_WINS = 0
TOTAL_LOSSES = 0
TOTAL_PNL = 0.0
daily_start_eq = CAPITAL
last_date = None
stopped_out = False
stop_reason = ""
allowed_new_today = MAX_DAILY_TRADES
opened_today = 0
demo_done = False

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(b"OKX V41 LIVE")
    def log_message(self, *a): pass
threading.Thread(target=lambda: HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 8000))), H).serve_forever(), daemon=True).start()

def tg(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      data={"chat_id": TG_CHAT, "text": msg, "parse_mode": "HTML"}, timeout=15)
    except: pass

def okx_server_time():
    try:
        r = requests.get(f"{BASE_URL}/api/v5/public/time", timeout=5)
        if r.status_code == 200:
            data = r.json()
            if data.get("code") == "0":
                return float(data["data"][0]["ts"]) / 1000.0
    except: pass
    return time.time()

def okx_timestamp():
    server_ts = okx_server_time()
    return datetime.utcfromtimestamp(server_ts).isoformat(timespec="milliseconds") + "Z"

def okx_sign(timestamp, method, path, body_str=""):
    message = f"{timestamp}{method}{path}{body_str}"
    mac = hmac.new(SECRET_KEY.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(mac).decode()

def okx_request(method, path, body=None):
    timestamp = okx_timestamp()
    body_str = "" if body is None else json.dumps(body)
    sign = okx_sign(timestamp, method, path, body_str)
    headers = {
        "OK-ACCESS-KEY": API_KEY,
        "OK-ACCESS-SIGN": sign,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": PASSPHRASE,
        "Content-Type": "application/json"
    }
    url = BASE_URL + path
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, params=body, timeout=15)
        else:
            r = requests.post(url, headers=headers, data=body_str, timeout=15)
        if r.status_code == 200:
            return r.json()
        else:
            tg(f"⚠️ OKX HTTP {r.status_code}: {r.text[:200]}")
            return None
    except Exception as e:
        tg(f"⚠️ استثناء طلب OKX: {str(e)[:200]}")
        return None

def check_account_mode():
    res = okx_request("GET", "/api/v5/account/config")
    if res and res.get("code") == "0":
        acct_mode = res["data"][0].get("acctLv", "unknown")
        tg(f"i️ وضع الحساب: {acct_mode}")

def get_klines(instId, bar="4H", limit=200):
    res = okx_request("GET", f"/api/v5/market/candles?instId={instId}&bar={bar}&limit={limit}")
    return res["data"] if res and res.get("code") == "0" else []

def get_market_price(instId):
    res = okx_request("GET", f"/api/v5/market/ticker?instId={instId}")
    if res and res.get("code") == "0" and res["data"]:
        return float(res["data"][0]["last"])
    return 0.0

def place_order(instId, side, qty, tp=None, sl=None):
    body = {
        "instId": instId,
        "tdMode": "cross",
        "side": side,
        "ordType": "market",
        "sz": str(int(qty))
    }
    if tp: body["tpTriggerPx"] = str(tp)
    if sl: body["slTriggerPx"] = str(sl)
    return okx_request("POST", "/api/v5/trade/order", body)

# ========== مؤشرات V41 ==========
def calc_ema(series, period):
    if len(series) < period: return 0.0
    k = 2.0 / (period + 1)
    val = sum(series[:period]) / period
    for v in series[period:]: val = (v - val) * k + val
    return val

def calc_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1: return 0.0
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])) for i in range(1, len(closes))]
    return sum(trs[-period:]) / period

def calc_adx(highs, lows, closes, period=14):
    n = len(closes)
    if n < period * 2 + 1: return 0.0
    h, l, c = highs[-30:], lows[-30:], closes[-30:]
    tr = [max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])) for i in range(1, len(h))]
    pdm = [max(0, h[i]-h[i-1]) if h[i]-h[i-1] > l[i-1]-l[i] else 0 for i in range(1, len(h))]
    mdm = [max(0, l[i-1]-l[i]) if l[i-1]-l[i] > h[i]-h[i-1] else 0 for i in range(1, len(h))]
    atr_s = sum(tr[-period:]) / period
    if atr_s == 0: return 0.0
    pdi = 100 * sum(pdm[-period:]) / period / atr_s
    mdi = 100 * sum(mdm[-period:]) / period / atr_s
    denom = pdi + mdi
    return 100 * abs(pdi - mdi) / denom if denom != 0 else 0.0

def analyze(instId, candles):
    closes = [float(c[4]) for c in reversed(candles)]
    highs  = [float(c[2]) for c in reversed(candles)]
    lows   = [float(c[3]) for c in reversed(candles)]
    vols   = [float(c[5]) for c in reversed(candles)]
    if len(closes) < LOOKBACK + 3: return None

    recent_highs = highs[-LOOKBACK-2:-2]
    recent_lows  = lows[-LOOKBACK-2:-2]
    highest = max(recent_highs)
    lowest  = min(recent_lows)

    atr = calc_atr(highs[:-1], lows[:-1], closes[:-1], 14)
    if atr <= 0: return None

    adx = calc_adx(highs[:-1], lows[:-1], closes[:-1], 14)
    if adx < ADX_MIN: return None

    avg_vol = sum(vols[-LOOKBACK-2:-2]) / LOOKBACK
    if vols[-2] < avg_vol * 1.5: return None

    current_high = highs[-2]
    current_low  = lows[-2]
    direction = None
    entry_price = 0.0

    if current_high > highest:
        direction = 'buy'
        entry_price = highest
    elif current_low < lowest:
        direction = 'sell'
        entry_price = lowest

    if direction is None: return None

    if USE_TREND_FILTER:
        ema50 = calc_ema(closes[:-1], EMA_TREND_PERIOD)
        if ema50 <= 0: return None
        trend = 'Bullish' if closes[-2] > ema50 else 'Bearish'
        if direction == 'buy' and trend != 'Bullish': return None
        if direction == 'sell' and trend != 'Bearish': return None

    entry = entry_price * 1.0003 if direction == 'buy' else entry_price * 0.9997
    stop = entry - atr * STOP_MULT if direction == 'buy' else entry + atr * STOP_MULT
    target = entry + atr * TGT_MULT if direction == 'buy' else entry - atr * TGT_MULT
    return {"dir": direction, "entry": entry, "stop": stop, "target": target, "atr": atr}

def send_report(cycle):
    if stopped_out:
        tg(f"⛔ <b>الحساب متوقف</b>\nالسبب: {stop_reason}\nالرصيد: {usdt:.2f}$")
        return
    wr = (TOTAL_WINS / TOTAL_TRADES * 100) if TOTAL_TRADES > 0 else 0.0
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    daily_pnl = usdt - daily_start_eq
    total_pnl = usdt - CAPITAL
    lines = [
        f"📊 <b>OKX V41 #{cycle}</b> | {now}",
        f"━━━━━━━━━━━━━━━━━",
        f"💰 الرصيد: <b>{usdt:.2f}$</b> (رأس المال: {CAPITAL}$)",
        f"📈 أرباح اليوم: <b>{daily_pnl:+.2f}$</b> (الحد: {MAX_DAILY_LOSS}$)",
        f"📊 الأرباح الكلية: <b>{total_pnl:+.2f}$</b> (الحد: {MAX_TOTAL_LOSS}$)",
        f"📋 الصفقات: {TOTAL_TRADES} | ✅ {TOTAL_WINS} | ❌ {TOTAL_LOSSES}",
        f"🎯 WR: {wr:.1f}% | 📂 مفتوحة: {len(positions)}/{MAX_OPEN}",
        f"💡 مسموح اليوم: {allowed_new_today} صفقة جديدة"
    ]
    tg("\n".join(lines))

def run_demo_trade():
    global demo_done
    try:
        time.sleep(60)
        tg("🧪 <b>جاري فتح صفقة تجريبية على BTC-USDT-SWAP...</b>")
        price = get_market_price("BTC-USDT-SWAP")
        if price <= 0:
            tg("❌ فشل جلب سعر BTC للصفقة التجريبية")
            demo_done = True
            return
        qty = 1
        res = place_order("BTC-USDT-SWAP", "buy", qty)
        if res and res.get("code") == "0":
            tg(f"✅ <b>تم فتح الصفقة التجريبية</b>\n🟢 شراء BTC-USDT-SWAP\nالسعر: {price:.2f}\nالكمية: {qty} عقد")
        else:
            tg(f"❌ فشل فتح الصفقة التجريبية. رد OKX: {res}")
            demo_done = True
            return

        time.sleep(180)
        res_close = place_order("BTC-USDT-SWAP", "sell", qty)
        if res_close and res_close.get("code") == "0":
            tg("✅ <b>تم إغلاق الصفقة التجريبية بنجاح</b>")
        else:
            tg(f"⚠️ فشل إغلاق الصفقة التجريبية. رد OKX: {res_close}")
        demo_done = True
    except Exception as e:
        tg(f"⚠️ خطأ في الصفقة التجريبية: {traceback.format_exc()[:300]}")
        demo_done = True

if __name__ == "__main__":
    tg("🚀 <b>OKX V41 Demo Bot Active</b>\n💰 رأس المال: 5000$\n📊 7 عملات | فريم 4H\n⏳ سيتم فتح صفقة تجريبية بعد 60 ثانية...")
    check_account_mode()
    threading.Thread(target=run_demo_trade, daemon=True).start()

    last_close_time = {}
    cycle = 0

    while True:
        try:
            if stopped_out:
                time.sleep(900)
                continue
            cycle += 1

            for sym in SYMBOLS:
                candles = get_klines(sym, "4H", 120)
                if not candles or len(candles) < LOOKBACK + 3: continue

                close_time = candles[0][6]
                if sym in last_close_time and close_time == last_close_time[sym]:
                    continue
                last_close_time[sym] = close_time

                sig = analyze(sym, candles)
                if sig and len(positions) < MAX_OPEN and opened_today < allowed_new_today and demo_done:
                    dist = abs(sig["entry"] - sig["stop"])
                    qty = max(1, round(FIXED_RISK / dist))
                    res = place_order(sym, sig["dir"], qty, sig["target"], sig["stop"])
                    if res and res.get("code") == "0":
                        positions.append({
                            "sym": sym, "dir": sig["dir"],
                            "entry": sig["entry"], "stop": sig["stop"],
                            "target": sig["target"], "qty": qty
                        })
                        opened_today += 1
                        tg(f"🔔 <b>صفقة حقيقية</b>\n{'🟢 Long' if sig['dir']=='buy' else '🔴 Short'} {sym}\n📍 سعر: {sig['entry']:.4f}\n💵 كمية: {qty}")

            if cycle % 15 == 0 and demo_done:
                send_report(cycle)

            time.sleep(60)

        except Exception as e:
            tg(f"⚠️ خطأ: {traceback.format_exc()[:300]}")
            time.sleep(60)
