import os, requests, random, time
import json
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# 🟢 [版本號] v2.5 (2026 Update)
BOT_VERSION = "v2.5 (2026)"

# --- 1. 強力快取名單 (手動加入鈊象) ---
STOCK_CACHE = {
    "台積電": "2330", "tsmc": "2330", 
    "鴻海": "2317", "聯發科": "2454",
    "長榮": "2603", "陽明": "2609", "萬海": "2615",
    "廣達": "2382", "緯創": "3231", "技嘉": "2376", "廣明": "6188",
    "鈊象": "3293", "智原": "3035", "創意": "3443", "世芯": "3661"
}

# --- 2. 初始化 ---
line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

def call_gemini_2026(prompt):
    """
    v2.5: 針對 2026 年模型環境優化，使用 2.5 系列
    """
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys and os.environ.get('GEMINI_API_KEY'):
        keys = [os.environ.get('GEMINI_API_KEY')]
    
    random.shuffle(keys)
    last_error = "NoKeys"

    # 🚀 鎖定您帳號中確認存在的 2026 主流模型
    target_models = [
        "gemini-2.5-flash",       # 首選
        "gemini-flash-latest",    # 備援 (永遠指向最新版)
        "gemini-2.0-flash"        # 相容舊版
    ]

    for model in target_models:
        for key in keys:
            try:
                # 使用 v1beta 介面
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                headers = {'Content-Type': 'application/json'}
                # 使用 params 傳遞 key 比較安全，避免 404
                params = {'key': key}
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": 100}
                }
                
                time.sleep(random.uniform(0.5, 1.0))
                
                # 這裡改用 params=params
                response = requests.post(url, headers=headers, params=params, json=payload, timeout=6)
                
                if response.status_code == 200:
                    data = response.json()
                    text = data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                    if text: return text, "Active"
                else:
                    last_error = f"{response.status_code}"
                    print(f"⚠️ {model} 失敗: {response.status_code} {response.text}")
            except Exception as e:
                last_error = "Err"
                continue
    
    return None, f"Fail({last_error})"

def get_stock_id(u_input):
    # 1. 查快取 (鈊象現在會在這裡直接抓到)
    if u_input in STOCK_CACHE: return STOCK_CACHE[u_input]
    if u_input.isdigit() and len(u_input) >= 4: return u_input
    
    # 2. 查 AI (使用 v2.5)
    prompt = f"Identify Taiwan stock ID for '{u_input}'. Reply ONLY 4-digit ID."
    res, status = call_gemini_2026(prompt)
    
    if res and res.strip().isdigit():
        code = res.strip()
        STOCK_CACHE[u_input] = code
        return code
    return None

def fetch_price(stock_id):
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    # 設定抓取最近 5 天資料，確保避開假日空窗
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

    # 🛠️ 自我診斷指令 (若還是不行，輸入 debug 查看原因)
    if u_text.lower() == "debug":
        test_key = os.environ.get('GEMINI_API_KEY_1') or "NoKey"
        mask_key = test_key[:5] + "..." if len(test_key) > 5 else "None"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🔧 診斷資訊:\nVer: {BOT_VERSION}\nKey: {mask_key}\nIP: Zeabur Cloud"))
        return
    
    # A. 辨識
    stock_id = get_stock_id(u_text)
    if not stock_id:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 找不到「{u_text}」的代號\n(AI 連線失敗，請稍後再試)"))
        return

    # B. 報價
    data = fetch_price(stock_id)
    if not data:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 無 {stock_id} 報價資料"))
        return

    # C. AI 評語
    prompt = f"股票 {stock_id} 現價 {data['close']}。請用繁體中文給一句話短評(30字內)。"
    ai_ans, status = call_gemini_2026(prompt)
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
