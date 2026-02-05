import os
import requests
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

def call_gemini(prompt, model_type="flash"):
    from google import genai
    client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))
    # 統一使用 2.5 系列，Pro 處理 PK，Flash 處理單股與識別
    target_model = "gemini-2.5-pro" if model_type == "pro" else "gemini-2.5-flash"
    try:
        response = client.models.generate_content(model=target_model, contents=prompt)
        return response.text
    except Exception as e:
        return f"AI 暫時離線：{str(e)}"

def identify_symbols(user_input):
    prompt = f"將『{user_input}』轉為台股或美股代碼(如 2330.TW, 2409.TW)。只回傳代碼，用逗號隔開，不要多言。"
    result = call_gemini(prompt, model_type="flash")
    return [s.strip() for s in result.split(',') if s.strip()]

def fetch_stock_data(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1d"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        data = res.json()
        result = data['chart']['result'][0]
        closes = [round(c, 2) for c in result['indicators']['quote'][0]['close'] if c is not None]
        return {"name": symbol, "last": closes[-1], "trend": closes[-10:]} # 僅取 10 天數據
    except:
        return None

def analyze_and_compare(query):
    symbols = identify_symbols(query)
    all_data = [d for s in symbols if (d := fetch_stock_data(s))]
    
    if not all_data: return "找不到相關股票數據。"

    # 強制精簡與全面 PK 的 Prompt
    is_pk = len(all_data) > 1
    prompt = f"""
    數據：{all_data}
    請以專業分析師身份，針對上述{'所有' if is_pk else ''}股票進行{'橫向 PK' if is_pk else '快速診斷'}：
    1. **核心評比**：用 1 句話總結各股目前的技術/量價狀態。
    2. **決策建議**：{ '明確指出誰是首選及其理由' if is_pk else '給出壓力/支撐位與操作策略' }。
    
    限制：
    - 總字數 250 字內。
    - 禁止廢話開場（如：感謝您的提問）。
    - 使用繁體中文，多用項目符號。
    """
    return call_gemini(prompt, model_type="pro" if is_pk else "flash")

def get_recommendations():
    watchlist = ["2330.TW", "2317.TW", "2454.TW", "3481.TW", "2409.TW", "2603.TW"]
    candidates = [d for s in watchlist if (d := fetch_stock_data(s))]
    prompt = f"從以下數據挑出 2 支最穩健、具備多頭型態的股票，並簡述理由(200字內)：{candidates}"
    return "💡 **AI 今日穩健選股**\n\n" + call_gemini(prompt, model_type="flash")

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))
    line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))

    @handler.add(MessageEvent, message=TextMessage)
    def handle_message(event):
        user_text = event.message.text.strip()
        if any(w in user_text for w in ["你會什麼", "技能", "功能"]):
            reply_msg = "🤖 **功能：**\n1.「分析 股票(多支可)」：快速 PK 診斷\n2.「推薦」：穩健選股\n3. 支援台/美股名稱辨識"
        elif "推薦" in user_text:
            reply_msg = get_recommendations()
        elif "分析" in user_text:
            query = user_text.replace("分析", "").strip()
            reply_msg = analyze_and_compare(query)
        else:
            return # 不回應一般閒聊
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@app.route("/")
def home():
    return "精簡版助理運行中"
