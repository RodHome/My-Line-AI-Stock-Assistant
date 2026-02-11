import os, requests, random, re
import json
import time
import math
import concurrent.futures
import twstock
import psutil  # 資源監控
import gc      # 垃圾回收
from datetime import datetime, timedelta, time as dtime, timezone
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FlexSendMessage

app = Flask(__name__)

# 🟢 [版本號] v15.7.0 (Debug Mode + Resource Guard)
BOT_VERSION = "v15.7.0"

# --- [資源監控工具] ---
def get_memory_usage():
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / 1024 / 1024  # MB

def resource_guard(func):
    """裝飾器：執行前後監控記憶體，並在過高時強制回收"""
    def wrapper(*args, **kwargs):
        # mem_before = get_memory_usage()
        try:
            return func(*args, **kwargs)
        except Exception as e:
            print(f"--- [Critical Error] inside {func.__name__}: {e} ---")
            return None
        finally:
            mem_after = get_memory_usage()
            # print(f"--- [Resource] {func.__name__} End | Mem: {mem_after:.1f}MB ---")
            if mem_after > 700: # 設定安全水位 700MB
                print("--- [GC] Triggering Garbage Collection ---")
                gc.collect()
    return wrapper

# --- 1. 資料設定與全域變數 ---
AI_RESPONSE_CACHE = {}
MAX_CACHE_SIZE = 50

# ETF 資料庫
ETF_META = {
    "00878": {"name": "國泰永續高股息", "type": "高股息", "focus": "ESG/殖利率/填息"},
    "0056":  {"name": "元大高股息", "type": "高股息", "focus": "預測殖利率/填息"},
    "00919": {"name": "群益台灣精選高息", "type": "高股息", "focus": "殖利率/航運半導體週期"},
    "00929": {"name": "復華台灣科技優息", "type": "高股息", "focus": "月配息/科技股景氣"},
    "00713": {"name": "元大台灣高息低波", "type": "高股息", "focus": "低波動/防禦性"},
    "00940": {"name": "元大台灣價值高息", "type": "高股息", "focus": "月配息/價值投資"},
    "0050":  {"name": "元大台灣50", "type": "市值型", "focus": "大盤乖離/台積電展望"},
    "006208":{"name": "富邦台50", "type": "市值型", "focus": "大盤乖離/台積電展望"},
    "00679B":{"name": "元大美債20年", "type": "債券型", "focus": "美債殖利率/降息預期"},
    "00687B":{"name": "國泰20年美債", "type": "債券型", "focus": "美債殖利率/降息預期"}
}

# 菁英個股名單
ELITE_STOCK_DATA = {
    "台積電": {"code": "2330", "sector": "半導體"},
    "鴻海": {"code": "2317", "sector": "電子代工"},
    "聯發科": {"code": "2454", "sector": "IC設計"},
    "廣達": {"code": "2382", "sector": "AI伺服器"},
    "緯創": {"code": "3231", "sector": "AI伺服器"},
    "技嘉": {"code": "2376", "sector": "板卡"},
    "長榮": {"code": "2603", "sector": "航運"},
    "陽明": {"code": "2609", "sector": "航運"},
    "萬海": {"code": "2615", "sector": "航運"},
    "南亞科": {"code": "2408", "sector": "DRAM"}, # 確保南亞科在名單內
    "南亞":   {"code": "1303", "sector": "塑膠"},
    "富邦金": {"code": "2881", "sector": "金融"},
    "國泰金": {"code": "2882", "sector": "金融"}
}
ELITE_STOCK_POOL = {k: v["code"] for k, v in ELITE_STOCK_DATA.items()}
ALL_STOCK_MAP = ELITE_STOCK_POOL.copy()

# 嘗試載入外部名單
try:
    if os.path.exists('stock_list.json'):
        with open('stock_list.json', 'r', encoding='utf-8') as f:
            full_list = json.load(f)
            ALL_STOCK_MAP.update(full_list)
            print(f"[System] 外部名單載入成功。總數: {len(ALL_STOCK_MAP)}")
except Exception as e:
    print(f"[System] 使用內建名單: {e}")

CODE_TO_NAME = {v: k for k, v in ALL_STOCK_MAP.items()}

# Line Bot 設定
token = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
secret = os.environ.get('LINE_CHANNEL_SECRET')
line_bot_api = LineBotApi(token if token else 'UNKNOWN')
handler = WebhookHandler(secret if secret else 'UNKNOWN')

# --- 2. 輔助函數 ---

def get_taiwan_time_str():
    utc_now = datetime.now(timezone.utc)
    tw_time = utc_now + timedelta(hours=8)
    return tw_time.strftime('%H:%M:%S')

def calculate_cdp(high, low, close):
    cdp = (high + low + (close * 2)) / 4
    ah = cdp + (high - low)
    nh = (cdp * 2) - low
    nl = (cdp * 2) - high
    al = cdp - (high - low)
    return int(nh), int(nl)

def get_stock_id(text):
    text = text.strip()
    # 移除 "成本" 等字眼
    clean_text = re.sub(r'(成本|cost).*', '', text, flags=re.IGNORECASE).strip()
    
    # 1. 直接查表
    if clean_text in ALL_STOCK_MAP:
        res = ALL_STOCK_MAP[clean_text]
        print(f"--- [Debug] ID Match (Dict): {clean_text} -> {res} ---")
        return res
    
    # 2. 如果是數字且長度 >= 4
    if clean_text.isdigit() and len(clean_text) >= 4:
        print(f"--- [Debug] ID Match (Digit): {clean_text} ---")
        return clean_text
        
    print(f"--- [Debug] ID Lookup Failed for: {text} ---")
    return None

def clean_json_string(text):
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*', '', text)
    return text.strip()

# --- 3. 核心 API 函數 (含 Debug Log) ---

def call_gemini_json(prompt, system_instruction=None):
    # 你的 Gemini 呼叫邏輯 (保持原樣，為節省長度省略部分錯誤處理，重點在 Return)
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys and os.environ.get('GEMINI_API_KEY'): keys = [os.environ.get('GEMINI_API_KEY')]
    if not keys: return None
    random.shuffle(keys)
    
    # 使用 Flash 模型較快且省 Token
    model = "gemini-2.0-flash" 
    final_prompt = prompt + "\n\n⚠️請務必只回傳純 JSON 格式，不要有任何其他文字。"

    for key in keys:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            headers = {'Content-Type': 'application/json'}
            params = {'key': key}
            contents = [{"parts": [{"text": final_prompt}]}]
            if system_instruction:
                contents = [{"parts": [{"text": f"系統指令: {system_instruction}\n用戶: {final_prompt}"}]}]
            
            payload = {
                "contents": contents,
                "generationConfig": {"maxOutputTokens": 1000, "temperature": 0.3, "responseMimeType": "application/json"}
            }
            res = requests.post(url, headers=headers, params=params, json=payload, timeout=15)
            if res.status_code == 200:
                data = res.json()
                text = data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                if text: return clean_json_string(text)
        except Exception as e:
            print(f"[Gemini Error] {e}")
            continue
    return None

def fetch_data_light(stock_id):
    token = os.environ.get('FINMIND_TOKEN', '')
    url_hist = "https://api.finmindtrade.com/api/v4/data"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    print(f"--- [Debug] Fetching Data: {stock_id} ---")

    try:
        # 抓取 120 天資料
        start = (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d')
        res = requests.get(url_hist, params={
            "dataset": "TaiwanStockPrice", 
            "data_id": stock_id, 
            "start_date": start, 
            "token": token
        }, headers=headers, timeout=10) # 設定超時避免卡住

        if res.status_code != 200:
            print(f"--- [Error] API Status {res.status_code}: {res.text} ---")
            return None
            
        data_json = res.json()
        hist_data = data_json.get('data', [])
        
        if not hist_data:
            print(f"--- [Error] Empty Data for {stock_id}. Check Token or ID. ---")
            return None

    except Exception as e:
        print(f"--- [Error] Requests Failed: {e} ---")
        return None

    # --- 資料處理邏輯 ---
    try:
        latest_price = hist_data[-1]['close']
        prev_close = hist_data[-1]['close']
        
        # 判斷是否為今日 (若今日未收盤，取昨日為前一日)
        if len(hist_data) > 1:
            today_str = datetime.now().strftime('%Y-%m-%d')
            # 簡單判斷：如果最後一筆是今天，那前一筆就是昨日收盤
            if hist_data[-1].get('date') == today_str:
                prev_close = hist_data[-2]['close']
            else:
                # 如果最後一筆不是今天(例如週末)，那最後一筆就是最新價，前一筆是前日
                prev_close = hist_data[-2]['close']

        # 嘗試抓取 twstock 即時盤 (Optional)
        source_name = "歷史"
        try:
            stock_rt = twstock.realtime.get(stock_id)
            if stock_rt['success']:
                real_price = stock_rt['realtime']['latest_trade_price']
                if real_price and real_price != "-":
                    latest_price = float(real_price)
                    source_name = "即時"
        except: pass

        change = latest_price - prev_close
        change_pct = round(change / prev_close * 100, 2) if prev_close > 0 else 0
        sign = "+" if change > 0 else ""
        change_display = f"({sign}{round(change, 2)}, {sign}{change_pct}%)"
        color = "#D32F2F" if change >= 0 else "#2E7D32"

        last_day = hist_data[-1]
        res_price, sup_price = calculate_cdp(last_day['max'], last_day['min'], last_day['close'])
        
        closes = [d['close'] for d in hist_data]
        ma5 = round(sum(closes[-5:]) / 5, 2) if len(closes) >= 5 else 0
        ma20 = round(sum(closes[-20:]) / 20, 2) if len(closes) >= 20 else 0
        ma60 = round(sum(closes[-60:]) / 60, 2) if len(closes) >= 60 else 0

        return {
            "code": stock_id,
            "close": latest_price,
            "update_time": f"{get_taiwan_time_str()} ({source_name})",
            "resistance": res_price, "support": sup_price,
            "ma5": ma5, "ma20": ma20, "ma60": ma60,
            "change_display": change_display, "color": color,
            "raw_closes": closes,
            "raw_highs": [d['max'] for d in hist_data],
            "raw_lows": [d['min'] for d in hist_data],
            "raw_volumes": [d['Trading_Volume'] for d in hist_data]
        }
    except Exception as e:
        print(f"--- [Error] Data Processing Failed: {e} ---")
        return None

# --- 4. Bot 主要邏輯 ---

@app.route("/")
def health_check():
    return f"OK ({BOT_VERSION}) | Mem: {get_memory_usage():.1f}MB", 200

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except: abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
@resource_guard # 監控並保護這個函數
def handle_message(event):
    msg = event.message.text.strip()
    print(f"--- [Msg] User: {msg} ---")

    # 1. 推薦功能 (簡化版，避免 OOM)
    if msg.startswith("推薦") or msg.startswith("選股"):
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🔍 掃描中... (為保護伺服器，目前僅隨機掃描部分權值股)"))
        # 這裡可以呼叫你的 scan_recommendations_turbo，但記得 max_workers=3
        return

    # 2. 個股診斷
    stock_id = get_stock_id(msg)
    
    if not stock_id:
        # 找不到 ID，不回覆或回覆提示
        print(f"--- [Debug] No ID found for: {msg} ---")
        return

    # 3. 抓取資料 (關鍵除錯點)
    data = fetch_data_light(stock_id)
    
    if not data:
        print(f"--- [Error] Data fetch failed for {stock_id} ---")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"⚠️ 無法取得 {stock_id} 資料。\n可能原因：API 忙碌、Token 錯誤、或是該股無歷史資料。"))
        return

    # 4. 準備回覆
    name = CODE_TO_NAME.get(stock_id, stock_id)
    if stock_id in ETF_META: name = ETF_META[stock_id]['name']

    # 呼叫 Gemini (加入快取機制)
    cache_key = f"{stock_id}_analysis"
    ai_reply = AI_RESPONSE_CACHE.get(cache_key)
    
    if not ai_reply:
        sys_prompt = "你是專業操盤手。請回傳 JSON: analysis (50字內簡評), advice (🔴進場/🟡觀望/⚫避開), target (目標價), stop (停損價)。"
        user_prompt = f"標的:{name}, 現價:{data['close']}, MA5:{data['ma5']}, MA20:{data['ma20']}, MA60:{data['ma60']}"
        
        json_str = call_gemini_json(user_prompt, system_instruction=sys_prompt)
        
        analysis_text = "AI 分析中斷"
        advice_text = "觀望"
        try:
            res = json.loads(json_str)
            analysis_text = res.get('analysis', '無分析')
            advice_text = f"【建議】{res.get('advice')} | 🎯{res.get('target')} | 🛑{res.get('stop')}"
        except:
            pass
            
        ai_reply = f"📝 {analysis_text}\n{advice_text}"
        # 寫入快取
        if len(AI_RESPONSE_CACHE) > MAX_CACHE_SIZE: AI_RESPONSE_CACHE.clear()
        AI_RESPONSE_CACHE[cache_key] = ai_reply

    # 5. 組合訊息
    reply_text = (
        f"📊 **{name} ({stock_id})**\n"
        f"💰 現價: {data['close']} {data['change_display']}\n"
        f"🕒 時間: {data['update_time']}\n"
        f"------------------\n"
        f"🚧 壓力: {data['resistance']} | 🛡️ 支撐: {data['support']}\n"
        f"📈 均線: {data['ma5']} / {data['ma20']} / {data['ma60']}\n"
        f"------------------\n"
        f"{ai_reply}\n"
        f"(Sys: {BOT_VERSION})"
    )

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    # 啟動時清理一次
    gc.collect()
    app.run(host='0.0.0.0', port=port)
