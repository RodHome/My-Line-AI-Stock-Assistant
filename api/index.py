import os
import requests
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# --- 核心 AI 函數 ---
def call_gemini_pro(prompt):
    from google import genai
    api_key = os.environ.get('GEMINI_API_KEY')
    client = genai.Client(api_key=api_key)
    try:
        response = client.models.generate_content(model="gemini-2.5-pro", contents=prompt)
        return response.text
    except Exception as e:
        return f"AI 診斷異常：{str(e)}"

# --- 股票代碼辨識 (支援多筆) ---
def identify_symbols(user_input):
    # 將輸入拆分，並請 Pro 轉換成代碼清單
    prompt = f"請將以下輸入內容『{user_input}』轉換為台股代碼清單(如 2330.TW, 8069.TWO)或美股。只需回傳代碼，用逗號隔開。"
    result = call_gemini_pro(prompt)
    symbols = [s.strip() for s in result.split(',')]
    return symbols

# --- 抓取單股數據 ---
def fetch_stock_data(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1d"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        data = res.json()
        result = data['chart']['result'][0]
        closes = [round(c, 2) for c in result['indicators']['quote'][0]['close'] if c is not None]
        volumes = [v for v in result['indicators']['quote'][0]['volume'] if v is not None]
        return {"symbol": symbol, "closes": closes, "volumes": volumes}
    except:
        return None

# --- 多股比對與診斷 ---
def analyze_and_compare(query):
    symbols = identify_symbols(query)
    all_data = []
    for s in symbols:
        data = fetch_stock_data(s)
        if data: all_data.append(data)
    
    if not all_data: return "抱歉，找不到您提供的股票數據。"

    prompt = f"""
    你是一位精通技術指標與籌碼動向的頂級分析師。以下是多支股票的近期數據：
    {all_data}
    
    請進行以下工作：
    1. **單股診斷**：簡述各股目前在技術面(MA20、RSI)與量價(籌碼動向)的表現。
    2. **橫向比對**：若使用者提供多支股票，請明確指出哪一支目前的『風險報酬比』最優。
    3. **策略建議**：分別標出壓力位與支撐位，並給予繁體中文操作指引。
    """
    return call_gemini_pro(prompt)

# --- 智慧推薦功能 ---
def get_recommendations():
    # 預設觀察清單 (包含半導體、面板、代工等龍頭)
    watchlist = ["2330.TW", "2317.TW", "2454.TW", "3481.TW", "2409.TW", "2603.TW"]
    candidates = []
    for s in watchlist:
        data = fetch_stock_data(s)
        if data: candidates.append(data)

    prompt = f"""
    身為專業操盤手，請從以下清單中篩選出『目前最值得佈局』的 1-2 支股票：
    {candidates}
    
    條件：
    - 技術面：剛從底部放量攻擊，或回測月線有撐。
    - 籌碼面：量價配合良好，避免追高已爆量的股票。
    - 推薦理由：請詳述其技術與量價優勢，避免讓使用者買入即套牢。
    """
    return "💡 **今日 AI 嚴選推薦** 💡\n\n" + call_gemini_pro(prompt)

# --- 功能說明 ---
def show_skills():
    return """🤖 **我是您的 AI 股市大師 (Gemini 2.5 Pro 版本)**

我具備以下強大技能：

1️⃣ **深度單股分析**：輸入「分析 台積電」或「分析 2330」。
2️⃣ **多股強弱比對**：輸入「分析 群創 友達 2409」，我會幫您選出最優者。
3️⃣ **技術與籌碼診斷**：包含 MA 移動平均線、RSI 趨勢及主力價量判讀。
4. **AI 智慧推薦**：直接輸入「推薦」，我會篩選出目前線型與籌碼俱佳的穩健標的。

請問今天想診斷哪支股票呢？"""

# --- LINE 核心處理 ---
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))
    line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))

    @handler.add(MessageEvent, message=TextMessage)
    def handle_message(event):
        user_text = event.message.text.strip()
        
        if any(word in user_text for word in ["你會什麼", "技能", "功能", "幫助", "help"]):
            reply_msg = show_skills()
        elif "推薦" in user_text:
            reply_msg = get_recommendations()
        elif "分析" in user_text:
            query = user_text.replace("分析", "").strip()
            reply_msg = analyze_and_compare(query)
        else:
            reply_msg = "歡迎！您可以輸入「分析 股票代碼」或「推薦」來開始。輸入「你會什麼」看更多功能。"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@app.route("/")
def home():
    return "Gemini 2.5 Pro 旗艦助理運作中"
