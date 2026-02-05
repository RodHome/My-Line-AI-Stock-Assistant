import os
import requests
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# --- 延遲載入 AI 的函數 ---
def ask_gemini(prompt):
    # 只有在需要時才載入重型套件，避免 Vercel 啟動超時
    import google.generativeai as genai
    genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))
    model = genai.GenerativeModel('gemini-1.5-flash')
    response = model.generate_content(prompt)
    return response.text

def get_stock_analysis(stock_id):
    # 判斷代碼格式
    symbol = f"{stock_id}.TW" if stock_id.isdigit() and len(stock_id) == 4 else stock_id
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=7d&interval=1d"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        response = requests.get(url, headers=headers)
        data = response.json()
        result = data.get('chart', {}).get('result')
        
        if not result:
            return f"找不到股票代號 {stock_id}，請檢查輸入。"
            
        indicators = result[0]['indicators']['quote'][0]
        closes = [round(c, 2) for c in indicators['close'] if c is not None]
        volumes = [v for v in indicators['volume'] if v is not None]
        
        data_summary = f"最近五天收盤價: {closes[-5:]}\n最近五天成交量: {volumes[-5:]}"
        prompt = f"你是一位專業分析師。以下是 {stock_id} 的最新數據：\n{data_summary}\n請簡要分析走勢、並給予繁體中文操作建議。"
        
        # 呼叫 AI 函數
        return ask_gemini(prompt)
        
    except Exception:
        return "抱歉，目前抓取股市數據時發生技術錯誤。"

# --- LINE 路由設定 ---
@app.route("/")
def home():
    return "AI 股市助理：伺服器運作中！"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    
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
            reply_msg = "歡迎！請輸入「分析 2330」來開始。"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'
