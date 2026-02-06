import os, requests, random, time, re
import json
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# 🟢 [版本號] v5.0 (Pro-Analyst: 均線+量能+營收+籌碼)
BOT_VERSION = "v5.0 (Full-Analysis)"

# --- 1. 快取名單 (可自行擴充) ---
STOCK_CACHE = {
    "台積電": "2330", "tsmc": "2330", 
    "鴻海": "2317", "聯發科": "2454",
    "長榮": "2603", "陽明": "2609", "萬海": "2615",
    "廣達": "2382", "緯創": "3231", "技嘉": "2376",
    "群創": "3481", "友達": "2409", "中鋼": "2002"
}

line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

# --- 健康檢查 ---
@app.route("/")
def health_check():
    return "OK", 200

# --- AI 核心 ---
def call_gemini_v5(prompt):
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys and os.environ.get('GEMINI_API_KEY'):
        keys = [os.environ.get('GEMINI_API_KEY')]
    
    random.shuffle(keys)
    last_error = "NoKeys"
    max_tokens = 1200 # 稍微縮短 token 讓回應更精簡
    
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
                        "temperature": 0.3 # 低溫確保理性分析
                    }
                }
                
                time.sleep(random.uniform(0.3, 0.7)) # 稍微加快速度
                response = requests.post(url, headers=headers, params=params, json=payload, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    text = data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                    if text: return text.strip(), "Active"
                else:
                    last_error = f"{response.status_code}"
            except Exception as e:
                last_error = "Err"
                continue
    return None, f"Fail({last_error})"

# --- 輔助函式 ---
def clean_input(text):
    return re.sub(r"(建議|分析|買進|策略|怎麼看|分析一下)\s*", "", text).strip()

def get_stock_id(u_input):
    clean_name = clean_input(u_input)
    if clean_name in STOCK_CACHE: return STOCK_CACHE[clean_name]
    if clean_name.isdigit() and len(clean_name) == 4: return clean_name
    
    # 若快取找不到，問 AI (通常用於較冷門股票)
    prompt = f"Find the 4-digit stock code for Taiwan stock '{clean_name}'. Answer ONLY the 4 digits."
    res, status = call_gemini_v5(prompt)
    if res:
        match = re.search(r'\d{4}', res)
        if match:
            code = match.group(0)
            STOCK_CACHE[clean_name] = code
            return code
    return None

# --- 🔥 功能 1：基本面 (營收) ---
def fetch_revenue(stock_id):
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    start = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
    params = { "dataset": "TaiwanStockMonthRevenue", "data_id": stock_id, "start_date": start, "token": token }
    try:
        res = requests.get(url, params=params, timeout=5)
        data = res.json().get('data', [])
        if data:
            latest = data[-1]
            return f"{latest['revenue_month']}月營收年增 {latest['revenue_year_growth_rate']}%"
        return "營收持平"
    except: return "營收N/A"

# --- 🔥 功能 2：技術面 (MA + 量能) ---
def fetch_technical_data(stock_id):
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    # 抓 60 天確保 MA20 計算無誤
    start = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')
    params = { "dataset": "TaiwanStockPrice", "data_id": stock_id, "start_date": start, "token": token }
    try:
        res = requests.get(url, params=params, timeout=5)
        data = res.json().get('data', [])
        if not data: return None
        
        latest = data[-1]
        closes = [d['close'] for d in data]
        volumes = [d['Trading_Volume'] for d in data]
        
        # 1. 計算均線
        ma5 = round(sum(closes[-5:]) / 5, 2) if len(closes) >= 5 else 0
        ma20 = round(sum(closes[-20:]) / 20, 2) if len(closes) >= 20 else 0
        
        # 2. 計算量比 (今日量 / 過去5日均量)
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
            "trend": "多頭格局" if latest['close'] > ma20 else "空頭/整理"
        }
    except: return None

# --- 籌碼面 ---
def fetch_chips(stock_id):
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    start = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
    params = {"dataset": "TaiwanStockInstitutionalInvestorsBuySell", "data_id": stock_id, "start_date": start, "token": token}
    try:
        res = requests.get(url, params=params, timeout=5)
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
    
    # 步驟 1: 取得代號
    stock_id = get_stock_id(u_text)
    if not stock_id:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 找不到股票，請確認名稱。"))
        return

    # 步驟 2: 抓取技術面 (最重要，若無資料直接跳出)
    tech = fetch_technical_data(stock_id)
    if not tech:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 無 {stock_id} 股價資料"))
        return

    # 步驟 3: 抓取籌碼面
    chips = fetch_chips(stock_id)
    f_sheets = int(chips['foreign'] / 1000) # 換算成張數 (FinMind 單位是股)
    t_sheets = int(chips['trust'] / 1000)

    # 步驟 4: 抓取基本面
    revenue_info = fetch_revenue(stock_id)

    # 步驟 5: 🔥 建構資深分析師 Prompt
    # 這裡是最關鍵的「大腦」，教 AI 如何運用所有數據
    prompt = (
        f"角色：擁有20年台股經驗的資深操盤手。\n"
        f"標的：{stock_id}，現價 {tech['close']} 元。\n"
        f"【技術型態】：目前為{tech['trend']} (MA20月線: {tech['ma20']})，"
        f"今日量比 {tech['vol_ratio']} 倍 (成交 {int(tech['volume']/1000)} 張)。\n"
        f"【籌碼動向】：外資 {f_sheets} 張，投信 {t_sheets} 張。\n"
        f"【基本面】：{revenue_info}。\n"
        f"任務：請綜合以上三方面數據，給出犀利的操作建議。\n"
        f"分析邏輯：\n"
        f"1. 多頭訊號：股價 > MA20 且 量比 > 1.2 且 法人買超。\n"
        f"2. 警示訊號：量比過大(>3倍) 且 外資大買 -> 提醒隔日沖風險。\n"
        f"3. 空頭訊號：股價 < MA20 且 法人賣超。\n"
        f"4. 若營收大幅衰退，請警告不可長抱。\n"
        f"輸出要求：150字內，條列式重點，最後給出明確的「防守價位」(停損點)。"
    )
    
    # 步驟 6: 呼叫 AI 並回覆
    ai_ans, status = call_gemini_v5(prompt)
    
    # 顯示部分數據讓使用者參考
    reply = (
        f"📊 **{stock_id} 深度分析**\n"
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
