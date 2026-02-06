import os, requests, random, time, re
import json
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# 🟢 [版本號] v3.0.1 加入法人籌碼數據
BOT_VERSION = "v3.0.1 (Chips)"

# --- 1. 快取名單 (含熱門股與上櫃股) ---
STOCK_CACHE = {
    "台積電": "2330", "tsmc": "2330", 
    "鴻海": "2317", "聯發科": "2454",
    "長榮": "2603", "陽明": "2609", "萬海": "2615",
    "廣達": "2382", "緯創": "3231", "技嘉": "2376", "廣明": "6188",
    "鈊象": "3293", "智原": "3035", "創意": "3443", "世芯": "3661",
    "星宇": "2646", "星宇航空": "2646", "群創": "3481", "友達": "2409",
    "興富發": "2542"
}

line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

# --- AI 核心 ---
def call_gemini_v3(prompt):
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys and os.environ.get('GEMINI_API_KEY'):
        keys = [os.environ.get('GEMINI_API_KEY')]
    
    random.shuffle(keys)
    last_error = "NoKeys"
    
    # 使用目前最穩定的模型
    target_models = ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash-lite-001"]

    for model in target_models:
        for key in keys:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                headers = {'Content-Type': 'application/json'}
                params = {'key': key}
                
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "maxOutputTokens": 300,  # 增加長度以容納籌碼分析
                        "temperature": 0.3       # 低隨機性，確保分析精準
                    }
                }
                
                time.sleep(random.uniform(0.5, 1.2))
                response = requests.post(url, headers=headers, params=params, json=payload, timeout=8)
                
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

# --- 股票辨識 (Regex 強制濾網) ---
def get_stock_id(u_input):
    if u_input in STOCK_CACHE: return STOCK_CACHE[u_input]
    if u_input.isdigit():
        if len(u_input) == 4: return u_input
        return None 
    
    prompt = f"Find the 4-digit stock code for Taiwan stock '{u_input}'. Answer ONLY the 4 digits."
    res, status = call_gemini_v3(prompt)
    
    if res:
        match = re.search(r'\d{4}', res)
        if match:
            code = match.group(0)
            STOCK_CACHE[u_input] = code
            return code
    return None

# --- 抓取股價 ---
def fetch_price(stock_id):
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    start = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
    params = { "dataset": "TaiwanStockPrice", "data_id": stock_id, "start_date": start, "token": token }
    try:
        res = requests.get(url, params=params, timeout=5)
        data = res.json().get('data', [])
        return data[-1] if data else None
    except: return None

# --- 🆕 抓取籌碼 (外資/投信) ---
def fetch_chips(stock_id):
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    start = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
    
    # 這裡就是您要的資料庫名稱
    params = {
        "dataset": "TaiwanStockInstitutionalInvestorsBuySell", 
        "data_id": stock_id, 
        "start_date": start, 
        "token": token
    }
    
    try:
        res = requests.get(url, params=params, timeout=5)
        data = res.json().get('data', [])
        
        if not data: return {"foreign": 0, "trust": 0}

        # 找出最新日期
        latest_date = data[-1]['date']
        chips = {"foreign": 0, "trust": 0}
        
        # 篩選最新一天的法人數據
        for row in reversed(data):
            if row['date'] != latest_date: break
            
            # 這裡就是關鍵對應：FinMind 名稱 vs 我們的變數
            if row['name'] == 'Foreign_Investor':
                chips['foreign'] = row['buy'] - row['sell']
            elif row['name'] == 'Investment_Trust': # 👈 投信在這裡！
                chips['trust'] = row['buy'] - row['sell']
                
        return chips
    except: return {"foreign": 0, "trust": 0}

# --- LINE Webhook ---
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
    
    # 1. 辨識代號
    stock_id = get_stock_id(u_text)
    if not stock_id:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 找不到「{u_text}」"))
        return

    # 2. 抓股價
    price_data = fetch_price(stock_id)
    if not price_data:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 無 {stock_id} 股價資料"))
        return

    # 3. 🆕 抓籌碼
    chips_data = fetch_chips(stock_id)
    # 換算成「張」 (股數 / 1000)
    f_sheets = int(chips_data['foreign'] / 1000)
    t_sheets = int(chips_data['trust'] / 1000)

    # 4. AI 分析 (加入籌碼數據)
    prompt = (
        f"角色：資深台股分析師。\n"
        f"標的：{stock_id}，收盤價 {price_data['close']}。\n"
        f"籌碼：外資買賣超 {f_sheets} 張，投信買賣超 {t_sheets} 張。\n"
        f"要求：請用繁體中文，針對「價格」與「法人動向」給出 60 字內的短評。若投信大買請強調，若外資大賣請示警。"
    )
    
    ai_ans, status = call_gemini_v3(prompt)
    comment = ai_ans if ai_ans else "💡 AI 思考中..."
    
    # 5. 回覆
    reply = (
        f"📊 {stock_id} 收盤: {price_data['close']}\n"
        f"💰 外資: {f_sheets} 張\n"
        f"🏦 投信: {t_sheets} 張\n"
        f"------------------\n"
        f"🤖 評析: {comment}\n"
        f"(系統: {status} | {BOT_VERSION})"
    )
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
