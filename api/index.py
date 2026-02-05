import os
import requests
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import google.generativeai as genai

app = Flask(__name__)

# 設定環境變數
line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))
genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-1.5-flash')

def get_stock_analysis(stock_id):
    # 決定股票代碼格式
    symbol = f"{stock_id}.TW" if stock_id.isdigit() and len(stock_id) == 4 else stock_id
    
    # 使用輕量級的 requests 抓取 Yahoo Finance 資料
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=7d&interval=1d"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        response = requests.get(url, headers=headers)
        data = response.json()
        
        # 檢查是否有資料
        result = data.get('chart', {}).get('result')
        if not result:
            return f"找不到股票代號 {stock_id} 的數據，請檢查輸入。"
            
        # 提取最近幾天的收盤價
        indicators = result[0]['indicators']['quote'][0]
        closes = [round(c, 2) for c in indicators['close'] if c is not None]
        volumes = [v for v in indicators['volume'] if v is not None]
        
        data_summary = f"最近五天收盤價: {closes[-5:]}\n最近五天成交量: {volumes[-5:]}"
        
        # 丟給 Gemini 分析
        prompt = f"你是一位專業分析師。以下是 {stock_id} 的最新數據：\n{data_summary}\n請簡要分析走勢、指出支撐位與壓力位，並給予繁體中文的操作建議。"
        
        ai_response = model.generate_content(prompt)
        return ai_response.text
        
    except Exception as e:
        return f"分析時發生錯誤：助理目前無法連上股市數據庫。"

@app.route("/")
def home():
    return "LINE Bot 助理運行中！"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_text = event.message.text
    if "分析" in user_text:
        stock_id = user_text.replace("分析", "").strip()
        reply_msg = get_stock_analysis(stock_id)
    else:
        reply_msg = "歡迎使用 AI 助理！請輸入「分析 + 股票代碼」(如：分析 2330)。"
    
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))
