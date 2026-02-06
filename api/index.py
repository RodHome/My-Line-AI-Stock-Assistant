import os, requests, random, time
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from google import genai

app = Flask(__name__)

# --- 1. 配置 6 支金鑰與 LINE 憑證 ---
def call_gemini(prompt):
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys: keys = [os.environ.get('GEMINI_API_KEY')]
    random.shuffle(keys)
    for key in keys:
        try:
            client = genai.Client(api_key=key)
            # 隨機延遲避開集體限流
            time.sleep(random.uniform(0.1, 0.4))
            res = client.models.generate_content(model="models/gemini-2.0-flash", contents=prompt)
            if res.text: return res.text, "Active"
        except: continue
    return None, "Sleeping"

# --- 2. 核心功能：名稱辨識與資料抓取 ---
def get_stock_id(u_input):
    if u_input.isdigit() and len(u_input) >= 4: return u_input
    # 名稱轉代號
    res, _ = call_gemini(f"Identify Taiwan stock ID for '{u_input}'. Reply ONLY 4-digit ID or 'None'.")
    return res.strip() if res and res.strip().isdigit() else None

def fetch_price(stock_id):
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    start = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')
    params = {"dataset": "TaiwanStockPrice", "data_id": stock_id, "start_date": start, "token": token}
    try:
        data = requests.get(url, params=params, timeout=10).json().get('data', [])
        return data[-1] if data else None
    except: return None

# --- 3. Webhook 處理入口 ---
line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

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
    
    # A. 辨識代碼
    stock_id = get_stock_id(u_text)
    if not stock_id:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 無法辨識股票。"))
        return

    # B. 抓取現價
    data = fetch_price(stock_id)
    if not data:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 查無 {stock_id} 報價。"))
        return

    # C. AI 簡評
    prompt = f"股票 {stock_id} 現價 {data['close']}。請給一句話技術快評。"
    ai_ans, status = call_gemini(prompt)
    comment = ai_ans if ai_ans else "💡 AI 休息中，請參考即時報價。"
    
    reply = f"📊 **報價 ({stock_id})**\n現價: {data['close']}\n\n{comment}\n\n🏷️ 系統: {status}"
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    # 🎯 解決 Timeout 的核心：強制使用 Zeabur 指定的 8080 埠口
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
