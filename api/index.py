import os
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# 從 Zeabur 環境變數讀取 (務必確認變數名稱正確)
line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

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
    # 簡單的回應測試，若能回這句代表全線通車
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="✅ 環境對接成功！Zeabur 8080 運作正常。")
    )

if __name__ == "__main__":
    # 重要：配合 Zeabur 規範，強制監聽 8080
    app.run(host='0.0.0.0', port=8080)
