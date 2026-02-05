import os
import requests
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

def ask_gemini(prompt):
    from google import genai
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        return "錯誤：找不到 GEMINI_API_KEY"
    
    try:
        client = genai.Client(api_key=api_key)
        
        # 根據 image_bebf43.png，直接使用你 Key 支援的 2.5 版本
        response = client.models.generate_content(
            model="gemini-2.5-flash", 
            contents=prompt
        )
        return response.text
            
    except Exception as e:
        return f"AI 最終報錯：{str(e)}。請確認模型名稱是否正確。"

def get_stock_analysis(stock_id):
    # 判斷代碼格式 (台股 4 碼則補 .TW)
    symbol = f"{stock_id}.TW" if stock_id.isdigit() and len(stock_id) == 4 else stock_id
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=7d&interval=1d"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        result = data.get('chart', {}).get('result')
        if not result:
            return f"找不到股票代號 {stock_id}"
            
        closes = [round(c, 2) for c in result[0]['indicators']['quote'][0]['close'] if c is not None]
        data_summary = f"{stock_id} 最近五天收盤價: {closes[-5:]}"
        
        prompt = f"你是一位專業分析師。以下是數據：\n{data_summary}\n請給予繁體中文操作建議。"
        return ask_gemini(prompt)
    except Exception as e:
        return f"數據獲取失敗：{str(e)}"

@app.route("/")
def home():
    return "Gemini 2.5 AI 助理已就緒"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    
    handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))
    line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))

    @handler.add(MessageEvent, message=TextMessage)
    def handle_message(event):
        user_text = event.message.text
        if "分析" in user_text:
            stock_id = user_text.replace("分析", "").strip()
            reply_msg = get_stock_analysis(stock_id)
        else:
            reply_msg = "連線成功！請輸入「分析 2330」來測試最新的 Gemini 2.5。"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'
