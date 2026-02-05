import os
import requests
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# 讓 Gemini 幫我們找出正確的股票代碼
def identify_stock_symbol(user_input):
    from google import genai
    api_key = os.environ.get('GEMINI_API_KEY')
    client = genai.Client(api_key=api_key)
    
    # 判斷是否已經是 4 位數字，是的話直接回傳
    if user_input.isdigit() and len(user_input) == 4:
        return f"{user_input}.TW"
    
    # 如果是中文名稱，請 Gemini 轉換
    prompt = f"請幫我找出『{user_input}』的股票代碼。如果是台股請回傳格式如『2330.TW』，如果是美股請回傳如『AAPL』。請只回傳代碼，不要有任何解釋文字。"
    try:
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        symbol = response.text.strip()
        return symbol
    except:
        return None

def ask_gemini_analysis(symbol, data_summary):
    from google import genai
    api_key = os.environ.get('GEMINI_API_KEY')
    client = genai.Client(api_key=api_key)
    
    prompt = f"你是一位專業分析師。以下是股票 {symbol} 的最近五天收盤數據：\n{data_summary}\n請給予繁體中文的專業走勢分析與操作建議。"
    try:
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        return response.text
    except Exception as e:
        return f"分析失敗：{str(e)}"

def get_stock_analysis(user_input):
    # 第一步：找出正確的 Symbol
    symbol = identify_stock_symbol(user_input)
    if not symbol:
        return f"無法識別『{user_input}』對應的股票代碼。"

    # 第二步：去 Yahoo 抓資料
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=7d&interval=1d"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        result = data.get('chart', {}).get('result')
        if not result:
            return f"找不到股票代碼 {symbol} 的數據，請嘗試輸入代碼（例如：3481）。"
            
        closes = [round(c, 2) for c in result[0]['indicators']['quote'][0]['close'] if c is not None]
        data_summary = f"最近五天收盤價: {closes[-5:]}"
        
        # 第三步：分析
        return ask_gemini_analysis(symbol, data_summary)
    except Exception as e:
        return f"系統錯誤：{str(e)}"

@app.route("/")
def home():
    return "Gemini 2.5 智慧股市助理運作中"

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
            # 取得「分析」後面的關鍵字
            query = user_text.replace("分析", "").strip()
            reply_msg = get_stock_analysis(query)
        else:
            reply_msg = "歡迎！請輸入「分析 群創」或「分析 2330」。"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'
