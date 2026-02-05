import os
import requests
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# --- 核心 AI 調度員 ---
def call_gemini(prompt, model_type="flash"):
    from google import genai
    api_key = os.environ.get('GEMINI_API_KEY')
    client = genai.Client(api_key=api_key)
    
    # 根據需求選擇模型：推薦與識別用 Flash，深度分析用 Pro
    target_model = "gemini-2.5-pro" if model_type == "pro" else "gemini-1.5-flash"
    
    try:
        response = client.models.generate_content(model=target_model, contents=prompt)
        return response.text
    except Exception as e:
        if "429" in str(e):
            return "⚠️ 目前 AI 流量過大，請稍等 30 秒後再試，或嘗試單股分析。"
        return f"診斷異常：{str(e)}"

# --- 股票代碼辨識 (使用 Flash 節省配額) ---
def identify_symbols(user_input):
    prompt = f"將『{user_input}』轉為台股代碼清單(如 2330.TW, 2409.TW)。只回傳代碼，用逗號隔開。"
    result = call_gemini(prompt, model_type="flash")
    return [s.strip() for s in result.split(',')]

# --- 數據抓取 ---
def fetch_stock_data(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1d"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        data = res.json()
        result = data['chart']['result'][0]
        closes = [round(c, 2) for c in result['indicators']['quote'][0]['close'] if c is not None]
        return {"symbol": symbol, "closes": closes[-20:]} # 只取 20 天數據減少字數
    except:
        return None

# --- 分析與比對 (保留 Pro 的深度) ---
def analyze_and_compare(query):
    symbols = identify_symbols(query)
    all_data = []
    for s in symbols:
        data = fetch_stock_data(s)
        if data: all_data.append(data)
    
    if not all_data: return "找不到相關數據。"

    prompt = f"你是分析大師，請對比以下數據並給予專業繁體中文建議：{all_data}"
    # 如果只有一股，用 Flash 很快；多股比對則維持 Pro 的品質
    model = "pro" if len(all_data) > 1 else "flash"
    return call_gemini(prompt, model_type=model)

# --- 智慧推薦 (換成 Flash 以處理大量 Token) ---
def get_recommendations():
    watchlist = ["2330.TW", "2317.TW", "2454.TW", "3481.TW", "2409.TW", "2603.TW", "2881.TW"]
    candidates = []
    for s in watchlist:
        data = fetch_stock_data(s)
        if data: candidates.append(data)

    prompt = f"請從以下數據中，挑選出兩支線型最強、最穩健的股票並給予推薦理由：{candidates}"
    return "💡 **今日 AI 嚴選推薦 (Flash 速報)** 💡\n\n" + call_gemini(prompt, model_type="flash")

# --- 功能導覽 ---
def show_skills():
    return """🤖 **AI 股市大師 技能清單**

1️⃣ **單股/多股分析**：輸入「分析 台積電 鴻海」。
2️⃣ **智慧推薦**：直接輸入「推薦」，快速篩選潛力股。
3️⃣ **技術指標**：自動判斷 MA、RSI 與量價關係。

提示：若遇到 429 錯誤，代表免費版額度用完，請稍候片刻再試。"""

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))
    line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))

    @handler.add(MessageEvent, message=TextMessage)
    def handle_message(event):
        user_text = event.message.text.strip()
        if any(word in user_text for word in ["你會什麼", "功能", "技能"]):
            reply_msg = show_skills()
        elif "推薦" in user_text:
            reply_msg = get_recommendations()
        elif "分析" in user_text:
            query = user_text.replace("分析", "").strip()
            reply_msg = analyze_and_compare(query)
        else:
            reply_msg = "歡迎！請輸入「分析 + 股票」或「推薦」。"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@app.route("/")
def home():
    return "配議優化版運作中"
