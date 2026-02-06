import os, requests, random, time
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

def call_gemini_stable(prompt):
    """使用 6 支金鑰與 Flash 模型確保連通"""
    from google import genai
    api_keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not api_keys: api_keys = [os.environ.get('GEMINI_API_KEY')]
    
    random.shuffle(api_keys)
    for key in api_keys:
        try:
            client = genai.Client(api_key=key)
            # 隨機微小延遲防止雲端 IP 併發封鎖
            time.sleep(random.uniform(0.1, 0.3))
            res = client.models.generate_content(model="models/gemini-2.0-flash", contents=prompt)
            if res.text: return res.text, "Flash_OK"
        except: continue
    return "🚀 伺服器忙碌中，請 30 秒後重新輸入代號。", "Limit"

def get_stock_id_stable(u_input):
    """將名稱或混雜文字精準轉為 4 位代碼"""
    if u_input.isdigit() and len(u_input) >= 4: return u_input
    prompt = f"將『{u_input}』轉為台股代碼。只回傳 4 位數字，無法辨識回傳 None。"
    res, _ = call_gemini_stable(prompt)
    return res.strip() if res and res.strip().isdigit() else None

def fetch_price_stable(stock_id):
    """僅抓取當日收盤資料，保證不超載"""
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    start_date = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')
    params = {"dataset": "TaiwanStockPrice", "data_id": stock_id, "start_date": start_date, "token": token}
    try:
        data = requests.get(url, params=params, timeout=8).json().get('data', [])
        return data[-1] if data else None
    except: return None

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))
    line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))

    @handler.add(MessageEvent, message=TextMessage)
    def handle_message(event):
        u_text = event.message.text.strip()
        # 1. 智慧轉代碼
        stock_id = get_stock_id_stable(u_text)
        if not stock_id:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 無法辨識股票，請輸入名稱或代號。"))
            return

        # 2. 獲取報價
        price_data = fetch_price_stable(stock_id)
        if not price_data:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 查無代碼 {stock_id} 資料。"))
            return

        # 3. 極簡 AI 分析
        prompt = f"股票 {stock_id} 現價 {price_data['close']}。請給予一句話極簡分析。"
        ans, status = call_gemini_stable(prompt)
        
        reply = f"📊 **報價分析 ({stock_id})**\n現價: {price_data['close']}\n\n💡 快評:\n{ans}\n\n🏷️ 狀態: {status}"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

    try: handler.handle(body, signature)
    except: abort(400)
    return 'OK'
