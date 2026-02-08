import os
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot.exceptions import InvalidSignatureError

app = Flask(__name__)

# 從環境變數讀取設定
token = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
secret = os.environ.get('LINE_CHANNEL_SECRET')

# 如果沒設定變數，給個假的避免報錯 (但 LINE 會不通)
line_bot_api = LineBotApi(token if token else 'TEST_TOKEN')
handler = WebhookHandler(secret if secret else 'TEST_SECRET')

@app.route("/")
def health_check():
    return "LINE Bot 伺服器運作中！", 200

@app.route("/callback", methods=['POST'])
def callback():
    # 抓取 LINE 簽章
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# 簡單的回音功能：你說什麼，我就回什麼
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text
    reply_msg = f"收到測試訊號：{user_msg}"
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
