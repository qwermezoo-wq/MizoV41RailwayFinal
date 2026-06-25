import os, time, requests, json, hmac, hashlib, base64, threading
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

API_KEY    = "f784f3d4-5592-4eec-8f38-2cab50727421"
SECRET_KEY = "D765DD0CCDBD8B36F33B16963A1CE5E8"
PASSPHRASE = "Mizo@2026"
BASE_URL   = "https://www.okx.com"
TG_TOKEN   = "8887593469:AAFKDCeleWxHuBC4p6q-vJQMTJ5V1ff0Lts"
TG_CHAT    = "5230956729"

SYMBOLS = ["BTC-USDT","ETH-USDT","SOL-USDT","ADA-USDT","AVAX-USDT","RUNE-USDT","TRX-USDT"]
BINANCE_MAP = {
    "BTC-USDT":"BTCUSDT","ETH-USDT":"ETHUSDT","SOL-USDT":"SOLUSDT",
    "ADA-USDT":"ADAUSDT","AVAX-USDT":"AVAXUSDT","RUNE-USDT":"RUNEUSDT","TRX-USDT":"TRXUSDT"
}

CAPITAL=5000.0; FIXED_RISK=50.0; STOP_MULT=2.0; TGT_MULT=4.0
MAX_OPEN=4; MAX_DAILY_TRADES=4; VOL_MULT=1.5; ADX_MIN=20
LOOKBACK=20; EMA_PERIOD=50; SLIPPAGE=0.0003; COMMISSION=0.0004
MAX_DAILY_LOSS=250.0; MAX_TOTAL_LOSS=500.0

usdt=CAPITAL; positions=[]; TOTAL_TRADES=0; TOTAL_WINS=0; TOTAL_LOSSES=0; TOTAL_PNL=0.0
daily_start_eq=CAPITAL; last_date=None; stopped_out=False; stop_reason=""
allowed_new_today=MAX_DAILY_TRADES; opened_today=0

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OKX V41 LIVE")
    def log_message(self,*a): pass

threading.Thread(
    target=lambda: HTTPServer(("0.0.0.0",int(os.environ.get("PORT",8000))),H).serve_forever(),
    daemon=True
).start()

def tg(msg):
    for _ in range(3):
        try:
            requests.post("https://api.telegram.org/bot"+TG_TOKEN+"/sendMessage",
                data={"chat_id":TG_CHAT,"text":msg,"parse_mode":"HTML"},timeout=15)
            return
        except: time.sleep(3)

def okx_ts():
    try:
        r=requests.get(BASE_URL+"/api/v5/public/time",timeout=5)
        if r.status_code==200 and r.json().get("code")=="0":
            t=float(r.json()["data"][0]["ts"])/1000.0
            return datetime.utcfromtimestamp(t).strftime("%Y-%m-%dT%H:%M:%S.")+str(int((t%1)*1000)).zfill(3)+"Z"
    except: pass
    t=time.time()
    return datetime.utcfromtimestamp(t).strftime("%Y-%m-%dT%H:%M:%S.")+str(int((t%1)*1000)).zfill(3)+"Z"

def okx_sign(ts,method,path,body=""):
    return base64.b64encode(
        hmac.new(SECRET_KEY.encode(),(ts+method+path+body).encode(),hashlib.sha256).digest()
    ).decode()

def okx_hdrs(method,path,body=""):
    ts=okx_ts()
    return {
        "OK-ACCESS-KEY":API_KEY,"OK-ACCESS-SIGN":okx_sign(ts,method,path,body),
        "OK-ACCESS-TIMESTAMP":ts,"OK-ACCESS-PASSPHRASE":PASSPHRASE,
        "Content-Type":"application/json","x-simulated-trading":"1"
    }

def okx_get(path):
    try:
        return requests.get(BASE_URL+path,headers=okx_hdrs("GET",path),timeout=10).json()
    except Exception as e:
        return {"code":"-1","msg":str(e)}

def okx_post(path,body_dict):
    b=json.dumps(body_dict)
    try:
        return requests.post(BASE_URL+path,headers=okx_hdrs("POST",path,b),data=b,timeout=15).json()
    except Exception as e:
        return {"code":"-1","msg":str(e)}

def get_balance():
    r=okx_get("/api/v5/account/balance?ccy=USDT")
    try:
        if r.get("code")=="0":
            for d in r["data"][0]["details"]:
                if d["ccy"]=="USDT":
                    return float(d["availBal"])
    except: pass
    return CAPITAL

def get_price(sym):
    r=okx_get("/api/v5/market/ticker?instId="+sym)
    try:
        if r.get("code")=="0":
            return float(r["data"][0]["last"])
    except: pass
    bn=BINANCE_MAP.get(sym,"")
    if bn:
        try:
            r=requests.get("https://api.binance.com/api/v3/ticker/price?symbol="+bn,timeout=5)
            if r.status_code==200:
                return float(r.json()["price"])
        except: pass
    return 0.0

def buy_spot(sym, usdt_amount):
    # OKX Spot شراء بـ USDT: نستخدم sz=USDT مع tgtCcy=quote_ccy
    sz = str(round(usdt_amount, 2))
    r = okx_post("/api/v5/trade/order", {
        "instId": sym,
        "tdMode": "cash",
        "side": "buy",
        "ordType": "market",
        "sz": sz,
        "tgtCcy": "quote_ccy"
    })
    if r.get("code") == "0":
        oid = r["data"][0].get("ordId","OK")
        price = get_price(sym)
        qty = round(usdt_amount / price, 6) if price > 0 else 0
        return oid, qty
    tg("⚠️ فشل شراء "+sym+": "+str(r.get("msg",""))+" | "+str(r.get("data","")))
    return None, 0

def sell_spot(sym, qty):
    # بيع بالكمية الأصلية
    r = okx_post("/api/v5/trade/order", {
        "instId": sym,
        "tdMode": "cash",
        "side": "sell",
        "ordType": "market",
        "sz": str(qty),
        "tgtCcy": "base_ccy"
    })
    if r.get("code") == "0":
        return True
    tg("⚠️ فشل بيع "+sym+": "+str(r.get("msg",""))+" | "+str(r.get("data","")))
    return False

def check_risk():
    global daily_start_eq,last_date,stopped_out,stop_reason,allowed_new_today,opened_today
    today=datetime.now(timezone.utc).date()
    if last_date!=today:
        carried=len(positions)
        allowed_new_today=max(0,MAX_DAILY_TRADES-carried)
        daily_start_eq=usdt; last_date=today; opened_today=0
        if carried>0:
            tg("🌅 يوم جديد | "+str(carried)+" مفتوحة | مسموح "+str(allowed_new_today))
    if usdt-daily_start_eq<=-MAX_DAILY_LOSS:
        stopped_out=True; stop_reason="تجاوز حد الخسارة اليومي 250$"
        tg("⛔ "+stop_reason); return False
    if usdt<=CAPITAL-MAX_TOTAL_LOSS:
        stopped_out=True; stop_reason="تجاوز حد الخسارة التراكمي 500$"
        tg("⛔ "+stop_reason); return False
    return True

def ema(s,p):
    if len(s)<p: return 0.0
    k=2.0/(p+1); v=sum(s[:p])/p
    for x in s[p:]: v=(x-v)*k+v
    return v

def atr_calc(h,l,c,p=14):
    if len(c)<p+1: return 0.0
    trs=[max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])) for i in range(1,len(c))]
    return sum(trs[-p:])/p

def adx_calc(h,l,c,p=14):
    if len(c)<p*2+1: return 0.0
    h2=h[-30:]; l2=l[-30:]; c2=c[-30:]
    trs=[max(h2[i]-l2[i],abs(h2[i]-c2[i-1]),abs(l2[i]-c2[i-1])) for i in range(1,len(h2))]
    pdm=[max(0,h2[i]-h2[i-1]) if h2[i]-h2[i-1]>l2[i-1]-l2[i] else 0 for i in range(1,len(h2))]
    mdm=[max(0,l2[i-1]-l2[i]) if l2[i-1]-l2[i]>h2[i]-h2[i-1] else 0 for i in range(1,len(h2))]
    a=sum(trs[-p:])/p; pd=sum(pdm[-p:])/p; md=sum(mdm[-p:])/p
    if a==0: return 0.0
    pdi=100*pd/a; mdi=100*md/a; d=pdi+mdi
    return 100*abs(pdi-mdi)/d if d!=0 else 0.0

def klines(sym,lim=120):
    bn=BINANCE_MAP.get(sym,"")
    if not bn: return []
    try:
        r=requests.get("https://api.binance.com/api/v3/klines?symbol="+bn+"&interval=4h&limit="+str(lim),timeout=15)
        if r.status_code==200:
            return [{"h":float(k[2]),"l":float(k[3]),"c":float(k[4]),"v":float(k[5])} for k in r.json()]
    except: pass
    return []

def analyze(sym):
    kl=klines(sym,120)
    if len(kl)<LOOKBACK+3: return None
    cl=[x["c"] for x in kl]; hi=[x["h"] for x in kl]
    lo=[x["l"] for x in kl]; vl=[x["v"] for x in kl]
    highest=max(hi[-LOOKBACK-2:-2]); lowest=min(lo[-LOOKBACK-2:-2])
    a=atr_calc(hi[:-1],lo[:-1],cl[:-1]); d=adx_calc(hi[:-1],lo[:-1],cl[:-1])
    if a<=0 or d<ADX_MIN: return None
    avg_v=sum(vl[-LOOKBACK-2:-2])/LOOKBACK
    if vl[-2]<avg_v*VOL_MULT: return None
    direction=None; ep=0.0
    if hi[-2]>highest: direction="Long"; ep=highest
    elif lo[-2]<lowest: direction="Short"; ep=lowest
    if not direction: return None
    e50=ema(cl[:-1],EMA_PERIOD)
    if e50<=0: return None
    trend="Bull" if cl[-2]>e50 else "Bear"
    if direction=="Long" and trend!="Bull": return None
    if direction=="Short" and trend!="Bear": return None
    entry=ep*(1+SLIPPAGE) if direction=="Long" else ep*(1-SLIPPAGE)
    stop=entry-a*STOP_MULT if direction=="Long" else entry+a*STOP_MULT
    target=entry+a*TGT_MULT if direction=="Long" else entry-a*TGT_MULT
    dist=abs(entry-stop)
    if dist<=0: return None
    return {"dir":direction,"entry":entry,"stop":stop,"target":target,"atr":a,"adx":round(d,1)}

def send_report(cycle):
    if stopped_out:
        tg("⛔ <b>متوقف</b>: "+stop_reason); return
    wr=(TOTAL_WINS/TOTAL_TRADES*100) if TOTAL_TRADES>0 else 0.0
    now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    msg=("📊 <b>OKX V41 #"+str(cycle)+"</b> | "+now+"\n"
        "━━━━━━━━━━━━━━━━━\n"
        "💰 الرصيد: <b>"+"%.2f"%usdt+"$</b>\n"
        "📈 اليوم: <b>"+"%+.2f"%(usdt-daily_start_eq)+"$</b> (حد: 250$)\n"
        "📊 الكلي: <b>"+"%+.2f"%(usdt-CAPITAL)+"$</b> (حد: 500$)\n"
        "━━━━━━━━━━━━━━━━━\n"
        "📋 "+str(TOTAL_TRADES)+" صفقة | ✅"+str(TOTAL_WINS)+" ❌"+str(TOTAL_LOSSES)+" | WR:"+"%.1f"%wr+"%\n"
        "📂 مفتوحة: "+str(len(positions))+"/4 | مسموح: "+str(allowed_new_today)+"\n"
        "🛡️ مخاطرة: 50$ | يومي: 250$ | تراكمي: 500$")
    if positions:
        msg+="\n━━━━━━━━━━━━━━━━━\n📌 <b>مفتوحة:</b>"
        for p in positions:
            cur=get_price(p["sym"])
            if cur>0:
                unr=(cur-p["entry"])*p["qty"] if p["dir"]=="Long" else (p["entry"]-cur)*p["qty"]
                icon="🟢" if unr>=0 else "🔴"
                msg+="\n"+icon+" "+("Long" if p["dir"]=="Long" else "Short")+" "+p["sym"].replace("-USDT","")
                msg+=" | "+"%.4f"%p["entry"]+" → "+"%.4f"%cur+" | "+"%+.2f"%unr+"$"
    tg(msg)

def demo_trade():
    time.sleep(60)
    tg("🧪 <b>صفقة تجريبية — BTC-USDT Spot</b>\nجاري الفتح...")
    price=get_price("BTC-USDT")
    if price<=0:
        tg("❌ فشل جلب سعر BTC"); return
    tg("💰 سعر BTC الحالي: "+"%.2f"%price+"$")
    oid,qty=buy_spot("BTC-USDT",11.0)
    if oid:
        tg("✅ <b>تم فتح الصفقة التجريبية!</b>\n"
           "🟢 Long BTC-USDT Spot\n"
           "📊 دخول: "+"%.2f"%price+"$\n"
           "📦 كمية: "+str(qty)+" BTC\n"
           "⏳ سيتم الإغلاق بعد 3 دقائق...")
        time.sleep(180)
        exit_price=get_price("BTC-USDT")
        ok=sell_spot("BTC-USDT",qty)
        if ok:
            diff=(exit_price-price)*qty if exit_price>0 else 0
            tg("✅ <b>تم إغلاق الصفقة التجريبية!</b>\n"
               "📊 دخول: "+"%.2f"%price+"$\n"
               "📊 خروج: "+"%.2f"%exit_price+"$\n"
               "💰 فرق: "+"%+.4f"%diff+"$\n"
               "🚀 البوت جاهز للتداول الحقيقي!")
        else:
            tg("⚠️ فشل إغلاق الصفقة التجريبية")
    else:
        tg("❌ فشل فتح الصفقة التجريبية")

# ===== بدء التشغيل =====
usdt=get_balance()
tg("🚀 <b>OKX V41+Trend — تشغيل</b>\n"
   "━━━━━━━━━━━━━━━━━\n"
   "💰 الرصيد التجريبي: <b>"+"%.2f"%usdt+"$ USDT</b>\n"
   "📊 7 عملات | فريم 4H | Breakout + EMA50 + ADX\n"
   "🛡️ مخاطرة: 50$ | يومي: 250$ | تراكمي: 500$\n"
   "🧠 أقصى 4 صفقات يومياً\n"
   "🔄 يفحص كل دقيقة | تقرير كل 15 دقيقة\n"
   "🧪 صفقة تجريبية ستفتح بعد 60 ثانية...")

threading.Thread(target=demo_trade,daemon=True).start()

last_close={}; cycle=0
while True:
    try:
        if stopped_out: time.sleep(900); continue
        cycle+=1; check_risk()

        for sym in SYMBOLS:
            if stopped_out: break
            try:
                bn=BINANCE_MAP.get(sym,"")
                if not bn: continue
                r=requests.get("https://api.binance.com/api/v3/klines?symbol="+bn+"&interval=4h&limit=2",timeout=10)
                if r.status_code!=200: continue
                data=r.json()
                if len(data)<2: continue
                ct=data[-2][6]
                if sym in last_close and ct==last_close[sym]: continue
                last_close[sym]=ct
                sig=analyze(sym)
                already=any(p["sym"]==sym for p in positions)
                if sig and len(positions)<MAX_OPEN and not already and opened_today<allowed_new_today:
                    price=get_price(sym)
                    if price<=0: continue
                    oid,qty=buy_spot(sym,FIXED_RISK)
                    if oid:
                        positions.append({"sym":sym,"dir":sig["dir"],"entry":price,
                                          "stop":sig["stop"],"target":sig["target"],"qty":qty})
                        opened_today+=1
                        exp_p=round(abs(sig["target"]-price)*qty,2)
                        exp_l=round(abs(price-sig["stop"])*qty,2)
                        tg("🔔 <b>صفقة جديدة V41!</b>\n"
                           +("🟢 Long " if sig["dir"]=="Long" else "🔴 Short ")
                           +"<b>"+sym+"</b>\n"
                           "━━━━━━━━━━━━━━━━━\n"
                           "📍 دخول: <b>"+"%.4f"%price+"$</b>\n"
                           "🛑 وقف: <b>"+"%.4f"%sig["stop"]+"$</b>\n"
                           "🎯 هدف: <b>"+"%.4f"%sig["target"]+"$</b>\n"
                           "📈 ربح متوقع: <b>+"+"%.2f"%exp_p+"$</b>\n"
                           "📉 خسارة متوقعة: <b>-"+"%.2f"%exp_l+"$</b>\n"
                           "📦 كمية: "+"%.5f"%qty+" | ADX: "+str(sig["adx"]))
            except: pass

        for pos in list(positions):
            if stopped_out: break
            price=get_price(pos["sym"])
            if price<=0: continue
            hit=None
            if pos["dir"]=="Long":
                if price<=pos["stop"]: hit=pos["stop"]
                elif price>=pos["target"]: hit=pos["target"]
            else:
                if price>=pos["stop"]: hit=pos["stop"]
                elif price<=pos["target"]: hit=pos["target"]
            if hit:
                TOTAL_TRADES+=1
                sell_spot(pos["sym"],pos["qty"])
                pnl=(hit-pos["entry"])*pos["qty"] if pos["dir"]=="Long" else (pos["entry"]-hit)*pos["qty"]
                net=pnl-(pos["entry"]+hit)*pos["qty"]*COMMISSION
                usdt+=net; TOTAL_PNL+=net
                if net>0: TOTAL_WINS+=1
                else: TOTAL_LOSSES+=1
                positions.remove(pos)
                tg(("✅ ربح" if net>0 else "❌ خسارة")+" | "
                   +("Long" if pos["dir"]=="Long" else "Short")+" "+pos["sym"].replace("-USDT","")+"\n"
                   "📊 دخول: "+"%.4f"%pos["entry"]+"\n"
                   "📊 خروج: "+"%.4f"%hit+"\n"
                   "💰 صافي: "+"%+.2f"%net+"$ | رصيد: "+"%.2f"%usdt+"$")
                check_risk()

        if cycle%15==0: send_report(cycle)

    except Exception as e:
        tg("⚠️ خطأ: "+str(e)[:150]); time.sleep(30); continue
    time.sleep(60)
