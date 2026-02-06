import os, requests, random
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)
user_sessions = {}

def call_gemini(prompt, use_pro=False):
    from google import genai
    api_keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not api_keys: api_keys = [os.environ.get('GEMINI_API_KEY')]
    random.shuffle(api_keys)
    
    # 既然測試 200 OK，這裡我們確保優先使用連線成功的模型
    target_model = "models/gemini-2.0-flash" # 優先用 Flash 確保速度與連通
    for key in api_keys:
        try:
            client = genai.Client(api_key=key)
            res = client.models.generate_content(model=target_model, contents=prompt)
            return res.text, "Gemini_OK"
        except: continue
    return None, "Gemini_Limit"

def fetch_finmind_data(stock_id):
    """嚴格遵循官方 data_id 規範"""
    token = os.environ.get('FINMIND_TOKEN', '')
    start_date = (datetime.now() - timedelta(days=50)).strftime('%Y-%m-%d')
    url = "https://api.finmindtrade.com/api/v4/data"
    
    # 🎯 關鍵修正：確保 data_id 是純數字字串
    params = {
        "dataset": "TaiwanStockPrice",
        "data_id": stock_id,
        "start_date": start_date,
        "token": token
    }
    
    try:
        resp = requests.get(url, params=params, timeout=10)
        json_data = resp.json()
        if json_data.get('status') != 200 or not json_data.get('data'):
            return f"ERR_FINMIND_{json_data.get('status')}"
        
        hist = [d['close'] for d in json_data['data']]
        return {"close": hist[-1], "ma20": round(sum(hist[-20:])/20, 2) if len(hist)>=20 else 0}
    except Exception as e:
        return f"ERR_CONN_{str(e)[:10]}"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))
    line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))

    @handler.add(MessageEvent, message=TextMessage)
    def handle_message(event):
        u_text = event.message.text.strip()
        
        # 1. 識別是否為股票代碼
        if u_text.isdigit() and len(u_text) >= 4:
            data = fetch_finmind_data(u_text)
            
            # 2. 除錯回報邏輯
            if isinstance(data, str):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 資料抓取失敗: {data}\n(請檢查 Vercel 的 FINMIND_TOKEN 是否正確)"))
                return

            # 3. 呼叫測試通過的 Gemini
            prompt = f"代碼{u_text}，現價{data['close']}，MA20為{data['ma20']}。請給出極簡分析與操作建議。"
            ans, status = call_gemini(prompt)
            
            final_msg = f"**股票分析 ({u_text})**\n{ans}\n\n🏷️ 系統診斷: {status}"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=final_msg))

    try: handler.handle(body, signature)
    except: abort(400)
    return 'OK'
