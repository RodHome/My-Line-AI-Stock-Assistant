import os
import requests
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

def ask_gemini(prompt):
    import google.generativeai as genai
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        return "錯誤：找不到 GEMINI_API_KEY 環境變數"
    
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Gemini AI 報錯：{str(e)}"

def get_stock_analysis(stock_id):
    symbol = f"{stock_id}.TW" if stock_id.isdigit() and len(stock_id) == 4 else stock_id
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=7d&interval=1d"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            return f"Yahoo 數據抓取失敗，狀態碼：{response.status_code}"
            
        data = response.json()
        result = data.get('chart', {}).get('result')
        if not result:
            return f"找不到股票代號 {stock_id} 的數據。"
            
        indicators = result[0]['indicators']['quote'][0]
        closes = [round(c, 2) for c in indicators['close'] if c is not None]
        
        if not closes:
            return "抓取到的收盤價數據為空。"
            
        data_summary = f"最近五天收盤價: {closes[-5:]}"
        prompt = f"你是一位專業分析師。以下是 {stock_id} 的最新數據：\n{data_summary}\n請給予簡要分析。"
        
        return ask_gemini(prompt)
        
    except Exception as e:
        return f"數據處理過程發生崩潰：{str(e)}"

@app.route("/")
def home():
    return "伺服器運行中"

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
            reply_msg = "請輸入「分析 + 代碼」"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'
