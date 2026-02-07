import os, requests, random, time, re
import json
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# 🟢 [版本號] v5.9 (Value-Investing: EPS+PE)
BOT_VERSION = "v5.9 (EPS-Valuation)"

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
    "00919": "00919", "00940": "00940"
}

CODE_TO_NAME = {v: k for k, v in STOCK_CACHE.items()}

line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

@app.route("/")
def health_check():
    return "OK", 200

# --- AI 核心 ---
def call_gemini_v5_9(prompt, is_search=False):
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys and os.environ.get('GEMINI_API_KEY'):
        keys = [os.environ.get('GEMINI_API_KEY')]
    
    random.shuffle(keys)
    last_error = "NoKeys"
    max_tokens = 150 if is_search else 1000 
    
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
                        "temperature": 0.3 
                    }
                }
                
                # 限制 20 秒，避免 LINE Timeout
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
    
    prompt = f"Identify the 4-digit stock code for Taiwan stock '{clean_name}'. Reply ONLY with the 4-digit number."
    res, status = call_gemini_v5_9(prompt, is_search=True)
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

# --- 🔥 新功能：抓取累積 EPS (取代營收) ---
def fetch_eps(stock_id):
    # ETF 沒有 EPS
    if stock_id.startswith("00"): return "ETF無EPS"

    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    
    # 抓取過去 400 天的財報 (確保涵蓋完整一整年)
    start = (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d')
    params = { 
        "dataset": "TaiwanStockFinancialStatements", 
        "data_id": stock_id, 
        "start_date": start, 
        "token": token 
    }
    headers = {'User-Agent': 'Mozilla/5.0'}

    try:
        res = requests.get(url, params=params, headers=headers, timeout=8) # 財報資料少，8秒夠了
        data = res.json().get('data', [])
        
        if not data: return "EPS無資料"

        # 篩選出 type 為 EPS 的數據
        eps_data = [d for d in data if d['type'] == 'EPS']
        
        if not eps_data: return "EPS無資料"
        
        # 取得最新一筆的年份 (例如 2024)
        latest_year = eps_data[-1]['date'][:4]
        
        # 篩選出該年份的所有 EPS 並加總
        current_year_eps = [d['value'] for d in eps_data if d['date'].startswith(latest_year)]
        total_eps = sum(current_year_eps)
        
        # 找出是第幾季到第幾季 (例如 Q1-Q3)
        quarters = len(current_year_eps)
        q_str = f"Q1-Q{quarters}" if quarters > 1 else "Q1"
        
        return f"{latest_year}年{q_str}累計 {round(total_eps, 2)}元"

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

    # --- Debug 指令 ---
    if u_text.lower() == "debug":
        token = os.environ.get('FINMIND_TOKEN', '')
        # 測試 FinMind EPS 抓取
        test_msg = "連線測試中..."
        try:
            url = "https://api.finmindtrade.com/api/v4/data"
            params = { "dataset": "TaiwanStockFinancialStatements", "data_id": "2330", "start_date": "2023-01-01", "token": token }
            headers = {'User-Agent': 'Mozilla/5.0'}
            res = requests.get(url, params=params, headers=headers, timeout=5)
            if res.status_code == 200:
                test_msg = "✅ 財報數據連線成功"
            else:
                test_msg = f"❌ 失敗代碼 {res.status_code}"
        except Exception as e:
            test_msg = f"❌ 異常: {str(e)[:10]}"

        ai_res, ai_status = call_gemini_v5_9("Hi", is_search=True)
        
        reply = (
            f"🛠️ **v5.9 系統診斷**\n"
            f"Token: {'✅ 有' if token else '❌ 無'}\n"
            f"EPS連線: {test_msg}\n"
            f"AI連線: {ai_status}\n"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # --- 股票分析 ---
    stock_id = get_stock_id(u_text)
    if not stock_id:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 找不到「{u_text}」"))
        return

    stock_name = get_stock_name(stock_id, u_text)
    display_name = f"{stock_id} {stock_name}".strip()

    tech = fetch_technical_data(stock_id)
    if not tech:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 無 {stock_id} 資料"))
        return

    chips = fetch_chips(stock_id)
    f_sheets = int(chips['foreign'] / 1000)
    t_sheets = int(chips['trust'] / 1000)
    
    # 🔥 改抓 EPS
    eps_info = fetch_eps(stock_id)

    # 🔥🔥🔥 Prompt 升級：要求做估值分析 🔥🔥🔥
    prompt = (
        f"角色：資深台股分析師。\n"
        f"標的：{display_name}，現價 {tech['close']}。\n"
        f"數據：MA20={tech['ma20']}，量比={tech['vol_ratio']}倍，外資={f_sheets}張，投信={t_sheets}張。\n"
        f"獲利能力：{eps_info}。\n\n"
        f"【指令】：\n"
        f"1. **嚴禁廢話**：直接分析，不要開場白。\n"
        f"2. **估值判斷**：請利用 EPS 粗估本益比，判斷目前股價是「便宜」、「合理」還是「昂貴」。\n"
        f"3. **字數控制**：約 200 字，完整講完。\n\n"
        f"【分析內容】：\n"
        f"1. **量價與趨勢**：(判斷多空結構)\n"
        f"2. **估值與籌碼**：(重要：請根據 {eps_info} 評論目前股價是否合理？法人是否買單？)\n"
        f"3. **操作建議**：(進場/觀望策略)\n"
        f"4. **風險防守**：(停損點與隔日沖提醒)"
    )
    
    ai_ans, status = call_gemini_v5_9(prompt)
    
    # 🌟 版面調整：把營收那行換成 EPS
    reply = (
        f"📊 **{display_name} 估值分析**\n"
        f"💰 價: {tech['close']} | 量比: {tech['vol_ratio']}x\n"
        f"📈 月線: {tech['ma20']} ({tech['trend']})\n"
        f"🏦 外資: {f_sheets}張 | 投信: {t_sheets}張\n"
        f"💎 {eps_info}\n"  # 👈 這裡變成顯示 EPS
        f"------------------\n"
        f"{ai_ans}\n"
        f"------------------\n"
        f"(系統: Active | {BOT_VERSION})"
    )

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run()
