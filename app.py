import os
import sys
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError

app = Flask(__name__)

# 從環境變數讀取 LINE 密鑰
token = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
secret = os.environ.get('LINE_CHANNEL_SECRET')

# 設定 LINE Bot (如果沒密鑰會印出警告，但不會馬上崩潰)
if token and secret:
    line_bot_api = LineBotApi(token)
    handler = WebhookHandler(secret)
else:
    print("⚠️ 警告：找不到 LINE 密鑰，請檢查環境變數！")
    line_bot_api = None
    handler = None

@app.route("/")
def health_check():
    # 這是給 Zeabur 檢查用的首頁
    version_info = sys.version.split()[0]
    return f"<h1>🟢 LINE Bot 活著！</h1><p>Python 版本: {version_info} (應該要是 3.9.x)</p>"

@app.route("/callback", methods=['POST'])
def callback():
    # 這是 LINE 傳送訊息進來的入口
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    
    app.logger.info("Request body: " + body)

    try:
        if handler:
            handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# 學人精功能：收到文字訊息 -> 原封不動回傳
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event
