from flask import Flask

app = Flask(__name__)

@app.route("/")
def home():
    return "連線成功！助理伺服器已經醒了。"

@app.route("/callback", methods=['POST'])
def callback():
    return 'OK'
