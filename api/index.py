import os
import requests
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# --- 核心 AI 調度員 (全面升級 2026 規格) ---
def call_gemini(prompt, model_type="flash"):
    from google import genai
    api_key = os.environ.get('GEMINI_API_KEY')
    client = genai.Client(api_key=api_key)
    
    # 根據 image_bebf43.png，我們只用 2.5 系列
    # Pro 用於深度分析 (配額緊) / Flash 用於快速推薦 (配額鬆)
    target_model = "gemini-2.5-pro" if model_type == "pro" else "gemini-2.5-flash"
    
    try:
        response = client.models.generate_content(model=target_model, contents=prompt)
        return response.text
    except Exception as e:
        if "429" in str(e):
            return "⚠️ Gemini 2.5 Pro 流量已達免費版上限，請稍等 30 秒後再試。"
        return f"AI 系統回報：{str(e)}"

# --- 股票代碼辨識 (改用 2.5 Flash) ---
def identify_symbols(user_input):
    prompt = f"將『{user_input}』轉為台股代碼清單(如 2330.TW, 2409.TW)。只回傳代碼，用逗號隔開。"
    result = call_gemini(prompt, model_type="flash")
    # 簡單清理可能的無效字元
    return [s.strip() for s in result.replace('models/', '').split(',')]

# --- 數據抓取 ---
def fetch_stock_data(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1d"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        data = res.json()
        result = data['chart']['result'][0]
        closes = [round(c, 2) for c in result['indicators']['quote'][0]['close'] if c is not None]
        return {"symbol": symbol, "closes": closes[-15:]} # 縮減數據量以節省 Token
    except:
        return None

# --- 分析與比對 ---
def analyze_and_compare(query):
    symbols = identify_symbols(query)
    all_data = []
    for s in symbols:
        data = fetch_stock_data(s)
        if data: all_data.append(data)
    
    if not all_data: return "找不到該股票的數據，請確認名稱是否正確。"

    prompt = f"身為專業分析師，請對比以下數據並給予專業繁體中文建議，包含技術面與量價判讀：{all_data}"
    # 多股比對呼叫 Pro，單股分析呼叫 Flash 節省配額
    model = "pro" if len(all_data) > 1 else "flash"
    return call_gemini(prompt, model_type=model)

# --- 智慧推薦 (改用 2.5 Flash) ---
def get_recommendations():
    watchlist = ["2330.TW", "2317.TW", "2454.TW", "3481.TW", "2409.TW", "2603.TW", "2881.TW"]
    candidates = []
    for s in watchlist:
        data = fetch_stock_data(s)
        if data: candidates.append(data)

    prompt = f"請從以下數據中，挑選出兩支目前線型最穩健、最適合佈局的股票並詳述理由：{candidates}"
    return "💡 **今日 AI 嚴選推薦 (2.5 Flash 驅動)** 💡\n\n" + call_gemini(prompt, model_type="flash")

# --- 功能導覽 ---
def show_skills():
    return """🤖 **AI 股市大師 (2026 終極版)**

1️⃣ **智慧推薦**：直接輸入「推薦」。
2️⃣ **多股 PK**：輸入「分析 群創 友達 2409」。
3️⃣ **技術指標**：自動判斷趨勢與支撐壓力。

目前採用 Gemini 2.5 Pro 與 Flash 雙大腦協作！"""

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))
    line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))

    @handler.add(MessageEvent, message=TextMessage)
    def handle_message(event):
        user_text = event.message.text.strip()
        if any(word in user_text for word in ["你會什麼", "技能", "功能"]):
            reply_msg = show_skills()
        elif "推薦" in user_text:
            reply_msg = get_recommendations()
        elif "分析" in user_text:
            query = user_text.replace("分析", "").strip()
            reply_msg = analyze_and_compare(query)
        else:
            reply_msg = "歡迎！輸入「分析 股票」或「推薦」開始診斷。"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@app.route("/")
def home():
    return "2026 全系列 Gemini 2.5 版本部署成功"
