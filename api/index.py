import os, requests, random, time
import json
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# 🟢 [版本號] v2.4 強化上市櫃辨識
BOT_VERSION = "v2.4 (Full)"

# --- 1. 預設快取 (常用股) ---
# 這裡只是「起點」，查詢過的股票會自動加入這裡
STOCK_CACHE = {
    "台積電": "2330", "tsmc": "2330", "鴻海": "2317", "聯發科": "2454",
    "長榮": "2603", "陽明": "2609", "萬海": "2615", "廣達": "2382",
    "緯創": "3231", "技嘉": "2376", "廣明": "6188", "高端": "6547"
}

# --- 2. 初始化 ---
line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

def call_gemini_v2_4(prompt):
    """
    v2.4: 鎖定 Lite 模型，並針對搜尋做優化
    """
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys and os.environ.get('GEMINI_API_KEY'):
        keys = [os.environ.get('GEMINI_API_KEY')]
    
    random.shuffle(keys)
    last_error = "NoKeys"

    # 優先使用 Lite 模型 (速度快、IP 限制較寬鬆)
    target_models = ["gemini-2.0-flash-lite-001", "gemini-2.0-flash", "gemini-1.5-flash"]

    for model in target_models:
        for key in keys:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                headers = {'Content-Type': 'application/json'}
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": 100}
                }
                
                time.sleep(random.uniform(0.5, 1.2)) # 避開併發限制
                
                response = requests.post(url, headers=headers, json=payload, timeout=6)
                
                if response.status_code == 200:
                    data = response.json()
                    text = data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                    if text: return text, "Active"
                else:
                    last_error = f"{response.status_code}"
            except:
                last_error = "Err"
                continue
    
    return None, f"Fail({last_error})"

def get_stock_id(u_input):
    # 1. 查快取 (秒回)
    if u_input in STOCK_CACHE: return STOCK_CACHE[u_input]
    if u_input.isdigit() and len(u_input) >= 4: return u_input
    
    # 2. 查 AI (動態搜尋)
    # 💡 Prompt 優化：明確要求搜尋上市(TWSE)與上櫃(TPEX)
    prompt = f"Identify Taiwan stock ID (TWSE or TPEX) for '{u_input}'. Reply ONLY 4-digit ID."
    res, status = call_gemini_v2_4(prompt)
    
    if res and res.strip().isdigit():
        code = res.strip()
        STOCK_CACHE[u_input] = code # 📝 搜尋成功，自動加入快取！
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
    
    # 步驟 A: 辨識代號 (快取 -> AI)
    stock_id = get_stock_id(u_text)
    if not stock_id:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 找不到「{u_text}」的代號\n(可能是 AI 連線忙碌中)"))
        return

    # 步驟 B: 抓報價
    data = fetch_price(stock_id)
    if not data:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 無 {stock_id} 報價資料"))
        return

    # 步驟 C: AI 短評
    prompt = f"股票 {stock_id} 現價 {data['close']}。請用繁體中文給一句話短評(30字內)。"
    ai_ans, status = call_gemini_v2_4(prompt)
    comment = ai_ans if ai_ans else "💡 AI 連線異常，請參考上方報價。"
    
    reply = (
        f"📊 {stock_id} 收盤: {data['close']}\n"
        f"------------------\n"
        f"🤖 AI: {comment}\n"
        f"(系統: {status} | {BOT_VERSION})"
    )
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
