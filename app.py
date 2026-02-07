import os, requests, random, time, re
import json
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# 🟢 [版本號] v6.7 (Refined: 移除冗餘快取)
BOT_VERSION = "v6.7 (Clean-Cache)"

# --- 1. 搜尋快取 (中文別名 -> 代號) ---
# 數字代號由程式自動判斷，這裡只放「中文暱稱」
STOCK_CACHE = {
    # 電子
    "台積電": "2330", "tsmc": "2330", "鴻海": "2317", "聯發科": "2454",
    "廣達": "2382", "緯創": "3231", "技嘉": "2376", "台達電": "2308",
    "群創": "3481", "友達": "2409", "威剛": "3260", "中鋼": "2002",
    "興富發": "2542", "勤美": "1532", "台泥": "1101", "增你強": "2340",
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
    "國泰永續": "00878", "永續高股息": "00878",
    "復華科技": "00929", "科技優息": "00929",
    "群益精選": "00919", "精選高息": "00919",
    "台灣價值": "00940", "價值高息": "00940",
    "台灣5G": "00881"
}

# --- 2. 顯示名稱對照表 (代號 -> 正式全名) ---
DISPLAY_NAMES = {
    # ETF
    "0050": "元大台灣50",
    "0056": "元大高股息",
    "00878": "國泰永續高股息",
    "00929": "復華台灣科技優息",
    "00919": "群益台灣精選高息",
    "00940": "元大台灣價值高息",
    "00881": "國泰台灣5G+",
    "006208": "富邦台50",
    "00713": "元大台灣高息低波",
    # 個股 (常用大股)
    "2330": "台積電",
    "2317": "鴻海",
    "2454": "聯發科",
    "2603": "長榮",
    "2883": "凱基金"
}

# 建立反向查表
CODE_TO_NAME = {v: k for k, v in STOCK_CACHE.items()}

line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

@app.route("/")
def health_check():
    return "OK", 200

# --- AI 核心 ---
def call_gemini_v6_7(prompt, is_search=False):
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys and os.environ.get('GEMINI_API_KEY'):
        keys = [os.environ.get('GEMINI_API_KEY')]
    
    random.shuffle(keys)
    last_error = "NoKeys"
    max_tokens = 2000
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
                        # 安全閥：修剪未完成句子
                        if not is_search:
                            valid_endings = ('。', '！', '？', '.', '!', '?', '”', '"')
                            if not text.endswith(valid_endings):
                                last_period = max(text.rfind('。'), text.rfind('！'), text.rfind('？'))
                                if last_period != -1:
                                    text = text[:last_period+1]
                                    
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
    
    # 1. 先查快取 (中文別名)
    if clean_name in STOCK_CACHE: return STOCK_CACHE[clean_name]
    
    # 2. 數字直接回傳 (這裡處理了 0050, 2330 這種情況)
    if clean_name.isdigit() and len(clean_name) >= 4: return clean_name
    
    if len(clean_name) > 5: return None

    # 3. AI 辨識
    prompt = f"Identify the 4-digit stock code for Taiwan stock '{clean_name}'. Reply ONLY with the 4-digit number. If NOT stock, return nothing."
    res, status = call_gemini_v6_7(prompt, is_search=True) 
    if res and (match := re.search(r'\d{4}', res)):
        code = match.group(0)
        STOCK_CACHE[clean_name] = code
        CODE_TO_NAME[code] = clean_name
        return code
    
    return None

def get_stock_name(stock_id, user_input_name=None):
    # 1. 優先查 ETF/個股 正名表
    if stock_id in DISPLAY_NAMES:
        return DISPLAY_NAMES[stock_id]
    
    # 2. 查快取反向表 (中文別名)
    if stock_id in CODE_TO_NAME:
        name = CODE_TO_NAME[stock_id]
        if name != stock_id:
            return name
            
    # 3. 使用者輸入
    if user_input_name and not user_input_name.isdigit():
        return user_input_name
        
    return ""

# --- EPS 抓取 ---
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

    if u_text.lower() == "debug":
        token = os.environ.get('FINMIND_TOKEN', '')
        reply = f"🛠️ **v6.7 系統診斷**\nToken: {'✅OK' if token else '❌未設定'}\n快取: 已優化 (Clean)"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    stock_id = get_stock_id(u_text)

    if not stock_id:
        reply = (
            "🤖 請輸入 **股票代號** 或 **名稱**\n"
            "例如：「2330」、「台積電」、「0050」\n\n"
            "💡 我會幫您查詢：\n"
            "✅ 現價與量能\n"
            "✅ 籌碼動向\n"
            "✅ EPS / ETF 正名\n"
            "✅ 完整分析建議"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # --- 股票分析 ---
    stock_name = get_stock_name(stock_id, u_text)
    display_name = f"{stock_id} {stock_name}".strip()

    tech = fetch_technical_data(stock_id)
    if not tech:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 無 {stock_id} 資料"))
        return

    chips = fetch_chips(stock_id)
    f_sheets = int(chips['foreign'] / 1000)
    t_sheets = int(chips['trust'] / 1000)
    eps_info = fetch_eps(stock_id)

    prompt = (
        f"角色：資深分析師。\n"
        f"標的：{display_name}，現價 {tech['close']}。\n"
        f"數據：MA20={tech['ma20']}，量比={tech['vol_ratio']}倍，外資={f_sheets}張，投信={t_sheets}張，EPS={eps_info}。\n\n"
        f"【指令】：\n"
        f"1. **禁止Markdown**：用純文字列點。\n"
        f"2. **務必結尾**：講完整。\n"
        f"3. **架構**：\n"
        f"   (1) 趨勢與量價：\n"
        f"   (2) 估值與籌碼：\n"
        f"   (3) 操作建議：(明確進場/防守)\n"
    )
    
    ai_ans, status = call_gemini_v6_7(prompt)
    
    reply = (
        f"📊 **{display_name} 完整分析**\n"
        f"💰 價: {tech['close']} | 量比: {tech['vol_ratio']}x\n"
        f"📈 月線: {tech['ma20']} ({tech['trend']})\n"
        f"🏦 外資: {f_sheets}張 | 投信: {t_sheets}張\n"
        f"💎 {eps_info}\n"
        f"------------------\n"
        f"{ai_ans}\n"
        f"------------------\n"
        f"(系統: Active | {BOT_VERSION})"
    )

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run()
