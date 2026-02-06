import os, requests, random, time, re
import json
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# 🟢 [版本號] v2.7 (Regex 濾網 + 4碼強制鎖定)
BOT_VERSION = "v2.7 (Smart)"

# --- 1. 快取名單 (擴充熱門股，減少 AI 依賴) ---
STOCK_CACHE = {
    "台積電": "2330", "tsmc": "2330", 
    "鴻海": "2317", "聯發科": "2454",
    "長榮": "2603", "陽明": "2609", "萬海": "2615",
    "廣達": "2382", "緯創": "3231", "技嘉": "2376", "廣明": "6188",
    "鈊象": "3293", "智原": "3035", "創意": "3443", "世芯": "3661",
    "星宇": "2646", "星宇航空": "2646", "群創": "3481", "友達": "2409"
}

line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

def call_gemini_v2_7(prompt):
    """
    v2.7 優化：針對 2.5 Flash 的參數調校，避免鸚鵡學舌
    """
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys and os.environ.get('GEMINI_API_KEY'):
        keys = [os.environ.get('GEMINI_API_KEY')]
    
    random.shuffle(keys)
    last_error = "NoKeys"
    
    # 鎖定連線成功的模型
    target_models = ["gemini-2.5-flash", "gemini-flash-latest"]

    for model in target_models:
        for key in keys:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                headers = {'Content-Type': 'application/json'}
                params = {'key': key}
                
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "maxOutputTokens": 200,  # 加長長度
                        "temperature": 0.3       # 降低隨機性，讓回答更精準 (解決鸚鵡學舌)
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

def get_stock_id(u_input):
    # 1. 查快取
    if u_input in STOCK_CACHE: return STOCK_CACHE[u_input]
    
    # 2. 濾網機制：如果是數字，必須是 4 位數才放行
    if u_input.isdigit():
        if len(u_input) == 4: return u_input
        # 如果使用者輸入 "34" 這種怪數字，直接擋掉，不問 AI
        return None 
    
    # 3. 查 AI (Regex 提取模式)
    prompt = f"Find the 4-digit stock code for Taiwan stock '{u_input}'. Answer ONLY the 4 digits (e.g. 2330)."
    res, status = call_gemini_v2_7(prompt)
    
    if res:
        # 💡 核心修正：使用 Regex 抓取字串中的「連續4個數字」
        # 這樣就算 AI 回答 "是 3481 喔"，我們也能精準抓到 3481
        match = re.search(r'\d{4}', res)
        if match:
            code = match.group(0)
            STOCK_CACHE[u_input] = code # 加入快取
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
    
    # A. 辨識
    stock_id = get_stock_id(u_text)
    if not stock_id:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 找不到「{u_text}」的代號\n(請輸入完整名稱或 4 碼代號)"))
        return

    # B. 報價
    data = fetch_price(stock_id)
    if not data:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 無 {stock_id} 報價資料"))
        return

    # C. AI 評析 (加強角色設定，解決 '台積電(' 問題)
    prompt = (
        f"角色：資深股市分析師。\n"
        f"任務：分析股票 {stock_id}，收盤價 {data['close']}。\n"
        f"要求：用繁體中文給出 50 字以內的技術面短評與建議。語氣要專業果斷，不要重複股票名稱。"
    )
    
    ai_ans, status = call_gemini_v2_7(prompt)
    comment = ai_ans if ai_ans else "💡 AI 連線忙碌，請參考數據。"
    
    reply = (
        f"📊 {stock_id} 收盤: {data['close']}\n"
        f"------------------\n"
        f"🤖 評析: {comment}\n"
        f"(系統: {status} | {BOT_VERSION})"
    )
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
