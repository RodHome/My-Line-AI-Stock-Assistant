import os, requests, random, time, re
import json
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# 🟢 [版本號] v5.2 (Fix Revenue N/A)
BOT_VERSION = "v5.2 (Rev-Fix)"

# --- 1. 快取名單 ---
STOCK_CACHE = {
    "台積電": "2330", "tsmc": "2330", 
    "鴻海": "2317", "聯發科": "2454",
    "長榮": "2603", "陽明": "2609", "萬海": "2615",
    "廣達": "2382", "緯創": "3231", "技嘉": "2376",
    "群創": "3481", "友達": "2409", "中鋼": "2002",
    "興富發": "2542", "威剛": "3260", "勤美": "1532",
    "長榮航": "2618", "華航": "2610", "高鐵": "2633",
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
def call_gemini_v5_2(prompt, is_search=False):
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys and os.environ.get('GEMINI_API_KEY'):
        keys = [os.environ.get('GEMINI_API_KEY')]
    
    random.shuffle(keys)
    last_error = "NoKeys"
    max_tokens = 100 if is_search else 1200
    
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
                
                time.sleep(random.uniform(0.3, 0.7))
                response = requests.post(url, headers=headers, params=params, json=payload, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    text = data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                    if text: return text.strip(), "Active"
                else:
                    last_error = f"{response.status_code}"
            except:
                last_error = "Err"
                continue
    return None, f"Fail({last_error})"

# --- 輔助函式 ---
def clean_input(text):
    return re.sub(r"(建議|分析|買進|策略|怎麼看|分析一下)\s*", "", text).strip()

def get_stock_id(u_input):
    clean_name = clean_input(u_input)
    if clean_name in STOCK_CACHE: return STOCK_CACHE[clean_name]
    if clean_name.isdigit() and len(clean_name) >= 4: return clean_name
    
    prompt = (
        f"Identify the 4-digit stock code for Taiwan stock '{clean_name}'. "
        f"Reply ONLY with the 4-digit number. If unsure, return nothing."
    )
    res, status = call_gemini_v5_2(prompt, is_search=True)
    if res:
        match = re.search(r'\d{4}', res)
        if match:
            code = match.group(0)
            STOCK_CACHE[clean_name] = code
            CODE_TO_NAME[code] = clean_name
            return code
    return None

def get_stock_name(stock_id, user_input_name=None):
    if stock_id in CODE_TO_NAME: return CODE_TO_NAME[stock_id]
    if user_input_name and not user_input_name.isdigit(): return user_input_name
    return ""

# --- 🔥 功能修正：營收抓取 (防呆 + 延長超時) ---
def fetch_revenue(stock_id):
    # 1. ETF 防呆機制：如果是 00 開頭，直接回傳，不查 API
    if stock_id.startswith("00"):
        return "ETF無營收數據"

    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    
    # 2. 抓取範圍擴大一點，避免月初抓不到上個月
    start = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
    params = { "dataset": "TaiwanStockMonthRevenue", "data_id": stock_id, "start_date": start, "token": token }
    
    try:
        # 3. 延長 timeout 到 10 秒
        res = requests.get(url, params=params, timeout=10)
        
        # 4. 檢查狀態碼，若是 429 代表請求太多次
        if res.status_code == 429:
            return "API忙碌(429)"
            
        data = res.json().get('data', [])
        if data:
            latest = data[-1]
            return f"{latest['revenue_month']}月營收年增 {latest['revenue_year_growth_rate']}%"
        return "營收尚未更新"
    except Exception as e:
        print(f"Revenue Error: {e}") # 印出錯誤到 Log 方便除錯
        return "營收讀取失敗"

# --- 技術面 ---
def fetch_technical_data(stock_id):
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    start = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')
    params = { "dataset": "TaiwanStockPrice", "data_id": stock_id, "start_date": start, "token": token }
    try:
        res = requests.get(url, params=params, timeout=10)
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
        res = requests.get(url, params=params, timeout=10)
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
    
    stock_id = get_stock_id(u_text)
    if not stock_id:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 找不到「{u_text}」"))
        return

    stock_name = get_stock_name(stock_id, u_text)
    display_name = f"{stock_id} {stock_name}".strip()

    tech = fetch_technical_data(stock_id)
    if not tech:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 無 {stock_id} 股價資料"))
        return

    chips = fetch_chips(stock_id)
    f_sheets = int(chips['foreign'] / 1000)
    t_sheets = int(chips['trust'] / 1000)

    # 抓取營收 (已加入修正)
    revenue_info = fetch_revenue(stock_id)

    prompt = (
        f"角色：資深操盤手。\n"
        f"標的：{display_name}，現價 {tech['close']}。\n"
        f"【技術面】：\n"
        f"- 趨勢: {tech['trend']} (MA20: {tech['ma20']})\n"
        f"- 量能: 量比 {tech['vol_ratio']} 倍 (成交 {int(tech['volume']/1000)} 張)\n"
        f"【籌碼面】：外資 {f_sheets} 張，投信 {t_sheets} 張。\n"
        f"【基本面】：{revenue_info}。\n"
        f"任務：給出 150 字內的犀利建議。\n"
        f"判定邏輯 (務必解釋量比)：\n"
        f"1. 量比 > 2.0：攻擊量/爆量，注意位階。\n"
        f"2. 量比 < 0.8：窒息量/人氣退潮。\n"
        f"3. 營收若有數據，請納入判斷；若為ETF則忽略營收。\n"
        f"4. 提醒隔日沖風險。\n"
        f"格式：條列式，含「趨勢」、「量價」、「建議」。"
    )
    
    ai_ans, status = call_gemini_v5_2(prompt)
    
    reply = (
        f"📊 **{display_name} 深度分析**\n"
        f"💰 價: {tech['close']} | 量比: {tech['vol_ratio']}x\n"
        f"📈 月線: {tech['ma20']} ({tech['trend']})\n"
        f"🏦 外資: {f_sheets}張 | 投信: {t_sheets}張\n"
        f"📝 {revenue_info}\n"
        f"------------------\n"
        f"{ai_ans}\n"
        f"------------------\n"
        f"(AI分析師: {status})"
    )

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
