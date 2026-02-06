import os, requests, random, time
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from google import genai

app = Flask(__name__)

# --- 1. 初始化 LINE 與 AI (使用 6 支金鑰) ---
line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

def call_gemini(prompt):
    api_keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not api_keys: api_keys = [os.environ.get('GEMINI_API_KEY')]
    random.shuffle(api_keys)
    for key in api_keys:
        try:
            client = genai.Client(api_key=key)
            # 隨機延遲避開集體限流
            time.sleep(random.uniform(0.1, 0.4))
            res = client.models.generate_content(model="models/gemini-2.0-flash", contents=prompt)
            if res.text: return res.text, "Active"
        except: continue
    return None, "Limit"

# --- 2. 核心功能：股票代碼辨識與報價 ---
def get_stock_id(u_input):
    """支援代碼與名稱辨識"""
    if u_input.isdigit() and len(u_input) >= 4: return u_input
    res, _ = call_gemini(f"Identify Taiwan stock ID for '{u_input}'. Reply ONLY 4-digit ID or 'None'.")
    return res.strip() if res and res.strip().isdigit() else None

def fetch_price(stock_id):
    """抓取 FinMind 最新價格"""
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    start = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')
    params = {"dataset": "TaiwanStockPrice", "data_id": stock_id, "start_date": start, "token": token}
    try:
        data = requests.get(url, params=params, timeout=8).json().get('data', [])
        return data[-1] if data else None
    except: return None

# --- 3. 處理 LINE 訊息 ---
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
    
    # A. 識別股票
    stock_id = get_stock_id(u_text)
    if not stock_id:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 無法辨識該股票名稱或代號。"))
        return

    # B. 取得最新報價
    data = fetch_price(stock_id)
    if not data:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 查無 {stock_id} 的報價。"))
        return

    # C. AI 簡評
    prompt = f"股票 {stock_id} 現價 {data['close']}。請給予一句話技術快評。"
    ai_ans, status = call_gemini(prompt)
    comment = ai_ans if ai_ans else "💡 AI 目前忙碌中，請參考即時數據。"
    
    reply = f"📊 **報價分析 ({stock_id})**\n現價: {data['close']}\n\n{comment}\n\n🏷️ 系統: {status}"
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    # 強制監聽 8080 以配合 Zeabur 規範
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
