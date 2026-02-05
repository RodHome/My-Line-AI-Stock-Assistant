import os
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# 從 Vercel 環境變數讀取金鑰
LINE_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
LINE_SECRET = os.environ.get('LINE_CHANNEL_SECRET')

line_bot_api = LineBotApi(LINE_TOKEN)
handler = WebhookHandler(LINE_SECRET)

@app.route("/")
def home():
    return "助理目前正在待命，且已準備好接收 LINE 訊息！"

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
    # 這一步只是測試：你傳什麼，它就回什麼
    user_text = event.message.text
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=f"測試成功！你剛剛說的是：{user_text}"))

if __name__ == "__main__":
    app.run()
