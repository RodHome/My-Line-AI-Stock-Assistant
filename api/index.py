import os, requests, random, time, re
import json
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# 🟢 [版本號] v6.1 (Port-Fix: 修正連線埠與語法)
BOT_VERSION = "v6.1 (Port-Fix)"

# --- 1. 快取名單 ---
STOCK_CACHE = {
    # 電子
    "台積電": "2330", "tsmc": "2330", "鴻海": "2317", "聯發科": "2454",
    "廣達": "2382", "緯創": "3231", "技嘉": "2376", "台達電": "2308",
    "群創": "3481", "友達": "2409", "威剛": "3260", "中鋼": "2002",
    "興富發": "2542", "勤美": "1532",
    # 航運
    "長榮": "2603", "陽明": "2609", "萬海": "2615", "長榮航": "2618", "華航": "2610",
    # 金融
    "富邦金": "2881", "國泰金": "2882", "凱基金": "2883", "開發金": "2883",
    "玉山金": "2884", "元大金": "2885", "兆豐金": "2886", "台新金": "2887",
    "新光金": "2888", "永豐金": "2890", "中信金": "2891", "第一金": "2892",
    "合庫金": "5880", "華南金": "2880",
    # ETF
    "0050": "0050", "0056": "0056", "00878": "00878", "00929": "00929",
    "00919": "00919", "00940": "00940", "00881": "00881"
}

CODE_TO_NAME = {v: k for k, v in STOCK_CACHE.items()}

line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

@app.route("/")
def health_check():
    return "OK", 200

# --- AI 核心 ---
def call_gemini_v6(prompt, is_search=False):
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys and os.environ.get('GEMINI_API_KEY'):
        keys = [os.environ.get('GEMINI_API_KEY')]
    
    random.shuffle(keys)
    last_error = "NoKeys"
    # 降低 token 數量，強迫 AI 講重點，提高速度
    max_tokens = 600 if is_search else 800 
    
    target_models = ["gemini-2.5-flash", "gemini-2.0-flash-lite-001", "gemini-flash-latest"]

    for model in target_models:
        for key in keys:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                headers = {'Content-Type': 'application/json'}
                params = {'key': key}
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "maxOutputTokens": max_tokens, 
                        "temperature": 0.3 # 低溫加快收斂
                    }
                }
                
                # 🔥 限制 AI 只能思考 20 秒，保留 10 秒給網路傳輸
                response = requests.post(url, headers=headers, params=params, json=payload, timeout=20)
                
                if response.status_code == 200:
                    data = response.json()
                    text = data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                    if text: return text.strip(), "Active"
                else:
                    last_error = f"{response.status_code}"
            except:
                last_error = "Timeout"
                continue
    return None, f"Fail({last_error})"

# --- 輔助函式 ---
def clean_input(text):
    return re.sub(r"(建議|分析|買進|策略|怎麼看|分析一下)\s*", "", text).strip()

def get_stock_id(u_input):
    clean_name = clean_input(u_input)
    if clean_name in STOCK_CACHE: return STOCK_CACHE[clean_name]
    if clean_name.isdigit() and len(clean_name) >= 4: return clean_name
    
    # 限制 AI 找代碼的時間
    prompt = f"Identify the 4-digit stock code for Taiwan stock '{clean_name}'. Reply ONLY with the 4-digit number."
    res, status = call_gemini_v6(prompt, is_search=True)
    if res and (match := re.search(r'\d{4}', res)):
        code = match.group(0)
        STOCK_CACHE[clean_name] = code
        CODE_TO_NAME[code] = clean_name
        return code
    return None

def get_stock_name(stock_id, user_input_name=None):
    if stock_id in CODE_TO_NAME: return CODE_TO_NAME[stock_id]
    if user_input_name and not user_input_name.isdigit(): return user_input_name
    return ""

# --- 🔥 EPS 抓取 (極速版) ---
def fetch_eps(stock_id):
    if stock_id.startswith("00"): return "ETF無EPS"

    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    
    # 只抓最近 365 天，減少數據量
    start = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    params = { 
        "dataset": "TaiwanStockFinancialStatements", 
        "data_id": stock_id, 
        "start_date": start, 
        "token": token 
    }
    headers = {'User-Agent': 'Mozilla/5.0'}

    try:
        # 🔥 嚴格限制：只給 5 秒，抓不到就跳過！避免拖累整個機器人
        res = requests.get(url, params=params, headers=headers, timeout=5)
        
        if res.status_code != 200: return "EPS連線忙碌"

        data = res.json().get('data', [])
        if not data: return "EPS無資料"

        eps_data = [d for d in data if d['type'] == 'EPS']
        if not eps_data: return "EPS無資料"
        
        # 簡單加總最近 4 季
        recent_eps = [d['value'] for d in eps_data[-4:]] # 取最後4筆
        total_eps = sum(recent_eps)
        
        return f"近四季累計 {round(total_eps, 2)}元"

    except:
        return "EPS讀取逾時"

# --- 技術面 ---
def fetch_technical_data(stock_id):
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    start = (datetime.now() - timedelta(days=70)).strftime('%Y-%m-%d')
    params = { "dataset": "TaiwanStockPrice", "data_id": stock_id, "start_date": start, "token": token }
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        # 技術面最重要，給 8 秒
        res = requests.get(url, params=params, headers=headers, timeout=8)
        data = res.json().get('data', [])
        if not data: return None
        
        latest = data[-1]
        closes = [d['close'] for d in data]
        volumes = [d['Trading_Volume'] for d in data]
        
        ma5 = round(sum(closes[-5:]) / 5, 2) if len(closes) >= 5 else 0
        ma20 = round(sum(closes[-20:]) / 20, 2) if len(closes) >= 20 else 0
        
        if len(volumes) >= 6:
            vol_avg_5 = sum(volumes[-6:-1]) / 5
            vol_ratio = round(latest['Trading_Volume'] / vol_avg_5, 1) if vol_avg_5 > 0 else 0
        else:
            vol_ratio = 1.0

        return {
            "close": latest['close'],
            "volume": latest['Trading_Volume'],
            "ma5": ma5,
            "ma20": ma20,
            "vol_ratio": vol_ratio,
            "trend": "多頭" if latest['close'] > ma20 else "空頭"
        }
    except: return None

# --- 籌碼面 ---
def fetch_chips(stock_id):
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    start = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
    params = {"dataset": "TaiwanStockInstitutionalInvestorsBuySell", "data_id": stock_id, "start_date": start, "token": token}
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, params=params, headers=headers, timeout=8)
        data = res.json().get('data', [])
        if not data: return {"foreign": 0, "trust": 0}
        
        latest_date = data[-1]['date']
        chips = {"foreign": 0, "trust": 0}
        for row in reversed(data):
            if row['date'] != latest_date: break
            if row['name'] == 'Foreign_Investor': chips['foreign'] = row['buy'] - row['sell']
            elif row['name'] == 'Investment_Trust': chips['trust'] = row['buy'] - row['sell']
        return chips
    except: return {"foreign": 0, "trust": 0}

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except: abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    u_text = event.message.text.strip()

    # --- Debug 指令 (快速檢查) ---
    if u_text.lower() == "debug":
        token = os.environ.get('FINMIND_TOKEN', '')
        # 測試 FinMind EPS
        test_msg = "測試中..."
        try:
            url = "https://api.finmindtrade.com/api/v4/data"
            params = { "dataset": "TaiwanStockFinancialStatements", "data_id": "2330", "start_date": "2023-01-01", "token": token }
            headers = {'User-Agent': 'Mozilla/5.0'}
            res = requests.get(url, params=params, headers=headers, timeout=5)
            if res.status_code == 200:
                test_msg = "✅ 連線成功"
            else:
                test_msg = f"❌ 失敗({res.status_code})"
        except Exception as e:
            test_msg = f"❌ 異常 (Timeout)"

        reply = f"🛠️ **v6.1 診斷**\nToken: {'✅ 有' if token else '❌ 無'}\nEPS連線: {test_msg}"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # --- 股票分析 ---
    stock_id = get_stock_id(u_text)
    if not stock_id:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 找不到「{u_text}」"))
        return

    stock_name = get_stock_name(stock_id, u_text)
    display_name = f"{stock_id} {stock_name}".strip()

    # 平行處理概念：按順序抓，但每個都有嚴格超時限制
    tech = fetch_technical_data(stock_id)
    if not tech:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 無 {stock_id} 資料"))
        return

    chips = fetch_chips(stock_id)
    f_sheets = int(chips['foreign'] / 1000)
    t_sheets = int(chips['trust'] / 1000)
    
    # 抓 EPS (若超過5秒會直接回傳 "讀取逾時"，不會卡住)
    eps_info = fetch_eps(stock_id)

    # 🔥🔥🔥 Prompt 修正：要求「條列式、無廢話、重點分析」 🔥🔥🔥
    prompt = (
        f"角色：台股分析師。標的：{display_name}。\n"
        f"現價{tech['close']}，MA20={tech['ma20']}，量比{tech['vol_ratio']}倍。\n"
        f"外資{f_sheets}張，投信{t_sheets}張。獲利：{eps_info}。\n\n"
        f"【指令】：\n"
        f"1. **禁止打招呼**，直接列點分析。\n"
        f"2. **利用 {eps_info} 計算本益比位階(昂貴/便宜)**。\n"
        f"3. 總字數 200 字以內，不要寫長篇大論，以免超時。\n\n"
        f"【輸出格式】：\n"
        f"1. **趨勢與量價**：(簡短判斷)\n"
        f"2. **估值與籌碼**：(本益比分析)\n"
        f"3. **操作建議**：(進場/停損點)"
    )
    
    ai_ans, status = call_gemini_v6(prompt)
    
    # 組合訊息
    # ✅ 修正了您原本缺少 f" 的錯誤
    reply = (
        f"📊 **{display_name} 極速分析**\n"
        f"💰 價: {tech['close']} | 量比: {tech['vol_ratio']}x\n"
        f"📈 月線: {tech['ma20']} ({tech['trend']})\n"
        f"🏦 近一日籌碼:\n"
        f"外資: {f_sheets}張 | 投信: {t_sheets}張\n" 
        f"💎 {eps_info}\n"
        f"------------------\n"
        f"{ai_ans}\n"
        f"------------------\n"
        f"(系統: {status} | {BOT_VERSION})"
    )

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    # 🔥 關鍵修正：確保 Zeabur 外部連線可以進來
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
