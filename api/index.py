import os
from flask import Flask, request

app = Flask(__name__)

@app.route("/")
def home():
    # 檢查金鑰是否成功讀取（隱藏部分字串以保安全）
    token = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '未設定')
    secret = os.environ.get('LINE_CHANNEL_SECRET', '未設定')
    return f"伺服器狀態：正常<br>Token狀態：{token[:5]}...<br>Secret狀態：{secret[:5]}..."

@app.route("/callback", methods=['POST'])
def callback():
    # 這是給 LINE Verify 用的最簡化回應，確保回傳 200 (OK)
    return 'OK', 200
