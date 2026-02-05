import os
import requests
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

def ask_gemini(prompt):
    # 使用日誌建議的最新 google.genai 套件
    from google import genai
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        return "錯誤：找不到 GEMINI_API_KEY"
    
    try:
        # 新版 SDK 的啟動方式
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=prompt
        )
        return response.text
    except Exception as e:
        return f"AI 報錯：{str(e)}"

def get_stock_analysis(stock_id):
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
        
        prompt = f"你是一位專業分析師。以下是數據：\n{data_summary}\n請簡要給予繁體中文操作建議。"
        return ask_gemini(prompt)
    except Exception as e:
        return f"數據抓取失敗：{str(e)}"

@app.route("/")
def home():
    return "2026 版 AI 助理已就緒"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    
    # 確保環境變數正確
    line_secret = os.environ.get('LINE_CHANNEL_SECRET')
    line_token = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
    
    handler = WebhookHandler(line_secret)
    line_bot_api = LineBotApi(line_token)

    @handler.add(MessageEvent, message=TextMessage)
    def handle_message(event):
        user_text = event.message.text
        if "分析" in user_text:
            stock_id = user_text.replace("分析", "").strip()
            reply_msg = get_stock_analysis(stock_id)
        else:
            reply_msg = "歡迎！請輸入「分析 2330」來測試最新的 Gemini 1.5。"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'
