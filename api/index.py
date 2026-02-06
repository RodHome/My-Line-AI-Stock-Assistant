import os, requests, random
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)
user_sessions = {}

def call_gemini(prompt):
    from google import genai
    import time
    
    # 🎯 讀取 6 支金鑰
    api_keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not api_keys: api_keys = [os.environ.get('GEMINI_API_KEY')]
    
    random.shuffle(api_keys)
    # 🎯 優先使用你測試通過的 2.0 Flash
    target_model = "models/gemini-2.0-flash" 
    
    for idx, key in enumerate(api_keys, 1):
        try:
            client = genai.Client(api_key=key)
            res = client.models.generate_content(model=target_model, contents=prompt)
            if res.text: return res.text, f"Key_{idx}_Flash"
        except Exception as e:
            if "429" in str(e):
                time.sleep(0.5) # 🎯 遇到滿載，先睡一下再試下一支
                continue
            return f"Error: {str(e)[:20]}", "Err"
            
    return "⚠️ 目前 6 支金鑰皆受到 Google 流量限制 (429)，請於 30 秒後重新輸入代碼查詢。", "Gemini_Limit"

def fetch_finmind_data(stock_id):
    """根據截圖規範使用 data_id"""
    token = os.environ.get('FINMIND_TOKEN', '')
    start_date = (datetime.now() - timedelta(days=50)).strftime('%Y-%m-%d')
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {"dataset": "TaiwanStockPrice", "data_id": stock_id, "start_date": start_date, "token": token}
    
    try:
        resp = requests.get(url, params=params, timeout=10).json()
        if resp.get('status') == 200 and resp.get('data'):
            data_list = resp['data']
            hist = [d['close'] for d in data_list]
            return {"now": hist[-1], "ma20": round(sum(hist[-20:])/20, 2), "vol": data_list[-1]['Trading_Volume']}
        return None
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
        
        if u_text.isdigit() and len(u_text) >= 4:
            # 1. 抓取資料 (此部分已確認正確)
            data = fetch_finmind_data(u_text)
            if not data:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 查無代碼或資料擷取失敗"))
                return

            # 2. 構建分析請求
            prompt = f"分析台股 {u_text}：現價{data['now']}，MA20為{data['ma20']}，成交量{data['vol']}。請給出極簡短的操作建議。"
            ans, status = call_gemini(prompt)
            
            # 3. 輸出回覆
            final_msg = f"**股票分析 ({u_text})**\n{ans}\n\n🏷️ 診斷: {status}"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=final_msg))

    try: handler.handle(body, signature)
    except: abort(400)
    return 'OK'
