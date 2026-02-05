import os
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import google.generativeai as genai
import yfinance as yf

app = Flask(__name__)

# 從環境變數讀取金鑰（稍後我們會在 Vercel 平台上設定這些）
line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))
genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-1.5-flash')

def get_stock_analysis(stock_id):
    # 判斷是否為台股代碼 (4位數字)
    ticker_id = f"{stock_id}.TW" if stock_id.isdigit() and len(stock_id) == 4 else stock_id
    try:
        stock = yf.Ticker(ticker_id)
        df = stock.history(period="7d")
        
        if df.empty:
            return f"找不到股票代號 {stock_id} 的數據，請檢查輸入是否正確（例如：2330）。"
        
        # 整理數據給 Gemini
        data_summary = df[['Close', 'Volume']].tail(5).to_string()
        prompt = f"你是一位專業分析師。以下是 {stock_id} 最近五天的股價與成交量數據：\n{data_summary}\n請簡要分析走勢、指出支撐位與壓力位，並給予建議。請用繁體中文回覆。"
        
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"分析時發生錯誤：{str(e)}"

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
    
    # 觸發分析邏輯
    if "分析" in user_text:
        stock_id = user_text.replace("分析", "").strip()
        reply_msg = get_stock_analysis(stock_id)
    else:
        reply_msg = f"你好！我是你的 AI 助理。輸入「分析 + 股票代碼」 (例如：分析 2330) 我就會幫你診斷喔！"
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply_msg)
    )
