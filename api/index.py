import os
import requests
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import google.generativeai as genai

app = Flask(__name__)

# 讀取環境變數
LINE_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
LINE_SECRET = os.environ.get('LINE_CHANNEL_SECRET')
GEMINI_KEY = os.environ.get('GEMINI_API_KEY')

line_bot_api = LineBotApi(LINE_TOKEN)
handler = WebhookHandler(LINE_SECRET)
genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

def get_stock_analysis(stock_id):
    # 判斷代碼格式 (台股 4 碼則補 .TW)
    symbol = f"{stock_id}.TW" if stock_id.isdigit() and len(stock_id) == 4 else stock_id
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=7d&interval=1d"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        response = requests.get(url, headers=headers)
        data = response.json()
        result = data.get('chart', {}).get('result')
        
        if not result:
            return f"找不到股票代號 {stock_id}，請確認輸入是否正確。"
            
        # 提取最近 5 天數據
        indicators = result[0]['indicators']['quote'][0]
        closes = [round(c, 2) for c in indicators['close'] if c is not None]
        volumes = [v for v in indicators['volume'] if v is not None]
        
        data_summary = f"最近五天收盤價: {closes[-5:]}\n最近五天成交量: {volumes[-5:]}"
        
        # 呼叫 Gemini 分析
        prompt = f"你是一位專業分析師。以下是 {stock_id} 的最新數據：\n{data_summary}\n請簡要分析走勢、支撐位與壓力位，並給予繁體中文建議。"
        ai_response = model.generate_content(prompt)
        return ai_response.text
        
    except Exception:
        return "抱歉，抓取股市數據時發生錯誤，請稍後再試。"

@app.route("/")
def home():
    return "AI 股市助理：運作中！請從 LINE 發送指令。"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
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
        # 先回覆一個「請稍候」的訊息，避免 AI 運算太久導致 LINE 超時
        reply_content = get_stock_analysis(stock_id)
    else:
        reply_content = "你好！我是你的 AI 股市助理。請輸入「分析 股票代碼」(例如：分析 2330) 讓我為你診斷。"
    
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_content))
