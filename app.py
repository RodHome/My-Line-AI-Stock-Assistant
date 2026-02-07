import os, requests, random, time, re
import json
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# 🟢 [版本號] v6.8 (v6.2 Base + Auto-Trim)
BOT_VERSION = "v6.8 (Complete-Fix)"

# --- 1. 快取名單 (保留 v6.2 的豐富名單) ---
STOCK_CACHE = {
    # 電子
    "台積電": "2330", "tsmc": "2330", "鴻海": "2317", "聯發科": "2454",
    "廣達": "2382", "緯創": "3231", "技嘉": "2376", "台達電": "2308",
    "群創": "3481", "友達": "2409", "威剛": "3260", "中鋼": "2002",
    "興富發": "2542", "勤美": "1532", "台泥": "1101", "增你強": "2340",
    "山隆": "2616", # 手動補上您常查的
    # 航運
    "長榮": "2603", "陽明": "2609", "萬海": "2615", "長榮航": "2618", "華航": "2610",
    # 金融
    "富邦金": "2881", "國泰金": "2882", "凱基金": "2883", "開發金": "2883",
    "玉山金": "2884", "元大金": "2885", "兆豐金": "2886", "台新金": "2887",
    "新光金": "2888", "永豐金": "2890", "中信金": "2891", "第一金": "2892",
    "合庫金": "5880", "華南金": "2880",
    # ETF (只留別名)
    "台灣50": "0050",
    "高股息": "0056",
    "國泰永續": "00878", "永續高股息": "00878", "復華科技": "00929", "科技優息": "00929",
    "群益精選": "00919", "精選高息": "00919", "台灣價值": "00940", "價值高息": "00940",
    "台灣5G": "00881"
}

CODE_TO_NAME = {v: k for k, v in STOCK_CACHE.items()}

line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

@app.route("/")
def health_check():
    return "OK", 200

# --- AI 核心 (加入自動修剪功能) ---
def call_gemini_v6_8(prompt, is_search=False):
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys and os.environ.get('GEMINI_API_KEY'):
        keys = [os.environ.get('GEMINI_API_KEY')]
    
    random.shuffle(keys)
    last_error = "NoKeys"
    
    # 給予充足空間
    max_tokens = 2000
    
    # 使用 Flash 模型，速度快且聽話
    target_models = ["gemini-1.5-flash", "gemini-2.0-flash-lite-001"]

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
                        "temperature": 0.3
                    }
                }
                
                response = requests.post(url, headers=headers, params=params, json=payload, timeout=25)
                
                if response.status_code == 200:
                    data = response.json()
                    text = data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                    text = text.strip()
                    
                    if text:
                        # 🔥🔥🔥 安全閥：自動修剪未完成的句子 🔥🔥🔥
                        if not is_search: 
                            # 如果結尾不是標點符號，代表斷句了
                            valid_endings = ('。', '！', '？', '.', '!', '?', '”', '"')
                            if not text.endswith(valid_endings):
                                # 往回找最後一個句號
                                last_period = max(text.rfind('。'), text.rfind('！'), text.rfind('？'))
                                if last_period != -1:
                                    # 只保留完整的部分
                                    text = text[:last_period+1]
                                else:
                                    # 如果連一個句號都沒有，強制加句號
                                    text += "。"
                                    
                        return text, "Active"
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
    # 1. 查快取
    if clean_name in STOCK_CACHE: return STOCK_CACHE[clean_name]
    # 2. 數字直接回傳
    if clean_name.isdigit() and len(clean_name) >= 4: return clean_name
    
    # 3. AI 模糊搜尋 (保留 v6.2 的強大搜尋能力)
    prompt = f"Identify the 4-digit stock code for Taiwan stock '{clean_name}'. Reply ONLY with the 4-digit number. If NOT stock, return nothing."
    res, status = call_gemini_v6_8(prompt, is_search=True) 
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

# --- EPS 抓取 (保留您喜歡的 v6.2/v5.9 邏輯) ---
def fetch_eps(stock_id):
    if stock_id.startswith("00"): return "ETF無EPS"
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    start = (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d')
    params = { "dataset": "TaiwanStockFinancialStatements", "data_id": stock_id, "start_date": start, "token": token }
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(url, params=params, headers=headers, timeout=6)
        data = res.json().get('data', [])
        if not data: return "EPS無資料"
        eps_data = [d for d in data if d['type'] == 'EPS']
        if not eps_data: return "EPS無資料"
        latest_year = eps_data[-1]['date'][:4]
        current_year_eps = [d['value'] for d in eps_data if d['date'].startswith(latest_year)]
        return f"{latest_year}累計 {round(sum(current_year_eps), 2)}元"
    except: return "EPS逾時"

# --- 技術面 ---
def fetch_technical_data(stock_id):
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    start = (datetime.now() - timedelta(days=70)).strftime('%Y-%m-%d')
    params = { "dataset": "TaiwanStockPrice", "data_id": stock_id, "start_date": start, "token": token }
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, params=params, headers=headers, timeout=8)
        data = res.json().get('data', [])
        if not data: return None
        latest = data[-1]
        closes = [d['close'] for d in data]
        volumes = [d['Trading_Volume'] for d in data]
        ma5 = round(sum(closes[-5:]) / 5, 2) if len(closes) >= 5 else 0
        ma20 = round(sum(closes[-20:]) / 20, 2) if len(closes) >= 20 else 0
        vol_ratio = round(latest['Trading_Volume'] / (sum(volumes[-6:-1])/5), 1) if len(volumes)>=6 else 1.0
        return {
            "close": latest['close'],
            "volume": latest['Trading_Volume'],
            "ma5": ma5, "ma20": ma20, "vol_ratio": vol_ratio,
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
        headers = {'User-Agent': 'Mozilla/5
