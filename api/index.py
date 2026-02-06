import os, requests, random, time, re
import json
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# 🟢 [版本號] v3.3 解鎖長文本 (解決回答中斷)
BOT_VERSION = "v3.3 (LongText)"

# --- 1. 快取名單 ---
STOCK_CACHE = {
    "台積電": "2330", "tsmc": "2330", 
    "鴻海": "2317", "聯發科": "2454",
    "長榮": "2603", "陽明": "2609", "萬海": "2615",
    "廣達": "2382", "緯創": "3231", "技嘉": "2376", "廣明": "6188",
    "鈊象": "3293", "智原": "3035", "創意": "3443", "世芯": "3661",
    "星宇": "2646", "星宇航空": "2646", "群創": "3481", "友達": "2409",
    "興富發": "2542", "中鋼": "2002"
}

line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

# --- 健康檢查 (防崩潰) ---
@app.route("/")
def health_check():
    return "OK", 200

# --- AI 核心 ---
def call_gemini_v3_3(prompt, is_detailed=False):
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys and os.environ.get('GEMINI_API_KEY'):
        keys = [os.environ.get('GEMINI_API_KEY')]
    
    random.shuffle(keys)
    last_error = "NoKeys"
    
    # 💡 關鍵修正：大幅增加 Token 上限
    # 一般模式 250 -> 400
    # 策略模式 600 -> 800 (確保能講完停損停利)
    max_tokens = 800 if is_detailed else 400
    
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
                        "maxOutputTokens": max_tokens, 
                        "temperature": 0.4 
                    }
                }
                
                time.sleep(random.uniform(0.5, 1.2))
                response = requests.post(url, headers=headers, params=params, json=payload, timeout=20) # 延長超時到 20秒
                
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
    if clean_name.isdigit():
        if len(clean_name) == 4: return clean_name
        return None 
    
    prompt = f"Find the 4-digit stock code for Taiwan stock '{clean_name}'. Answer ONLY the 4 digits."
    res, status = call_gemini_v3_3(prompt)
    if res:
        match = re.search(r'\d{4}', res)
        if match:
            code = match.group(0)
            STOCK_CACHE[clean_name] = code
            return code
    return None

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
    is_strategy_mode = any(k in u_text for k in ["建議", "分析", "買進", "策略"])
    
    stock_id = get_stock_id(u_text)
    if not stock_id:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 找不到股票，請確認名稱。"))
        return

    price_data = fetch_price(stock_id)
    if not price_data:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 無 {stock_id} 資料"))
        return

    chips_data = fetch_chips(stock_id)
    f_sheets = int(chips_data['foreign'] / 1000)
    t_sheets = int(chips_data['trust'] / 1000)

    if is_strategy_mode:
        # 💡 Prompt 優化：要求 AI 用「條列式」且「精簡」回答，避免廢話
        prompt = (
            f"角色：專業操盤手。\n"
            f"標的：{stock_id}，現價 {price_data['close']}。\n"
            f"籌碼：外資 {f_sheets} 張，投信 {t_sheets} 張。\n"
            f"任務：請給出操作策略，嚴格遵守以下格式，不要有前言廢話：\n"
            f"1.【趨勢】(多/空/盤整)\n"
            f"2.【區間】(建議進場價)\n"
            f"3.【停損】(價格)\n"
            f"4.【停利】(價格)\n"
            f"5.【短評】(30字內理由)"
        )
        ai_ans, status = call_gemini_v3_3(prompt, is_detailed=True)
        reply = f"📈 **{stock_id} 操盤策略**\n現價: {price_data['close']}\n------------------\n{ai_ans}\n------------------\n(系統: {status} | {BOT_VERSION})"
    else:
        prompt = (
            f"標的：{stock_id}，價 {price_data['close']}，外資{f_sheets}張，投信{t_sheets}張。"
            f"請給 50 字內籌碼短評，直接講結論，不要重複數據。"
        )
        ai_ans, status = call_gemini_v3_3(prompt, is_detailed=False)
        reply = f"📊 {stock_id} 收盤: {price_data['close']}\n💰 外資: {f_sheets} 張\n🏦 投信: {t_sheets} 張\n------------------\n🤖 {ai_ans}\n(💡 輸入「建議 {stock_id}」看操作策略)"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
