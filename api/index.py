import os
import requests
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# --- 核心 AI：根據任務自動切換型號 ---
def call_gemini(prompt, use_pro=False):
    from google import genai
    client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))
    # 推薦與辨識用 Flash (配額高/速度快)；深度 PK 用 Pro (邏輯強)
    model_name = "models/gemini-2.5-pro" if use_pro else "models/gemini-2.5-flash"
    try:
        response = client.models.generate_content(model=model_name, contents=prompt)
        return response.text
    except Exception as e:
        if "429" in str(e):
            return "⚠️ 流量達到免費版上限，請稍等 30 秒後再試。"
        return "AI 系統繁忙，請稍後再試。"

def identify_symbols(user_input):
    prompt = f"從『{user_input}』中提取股票名並轉為代碼(如 2330.TW)。只回傳代碼並用逗號隔開，嚴禁解釋。"
    result = call_gemini(prompt, use_pro=False)
    # 強制清洗：只保留數字、點、字母與逗號
    clean_result = "".join(c for c in result if c.isalnum() or c in ".,")
    return [s.strip() for s in clean_result.split(',') if s.strip()]

def fetch_stock_data(symbol):
    # 改抓 20 天數據以節省 Token
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1d"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(url, headers=headers, timeout=8)
        data = res.json()
        result = data['chart']['result'][0]
        closes = [round(c, 1) for c in result['indicators']['quote'][0]['close'] if c is not None]
        return {"id": symbol, "price": closes[-1], "trend": closes[-10:]}
    except:
        return None

def analyze_and_compare(query):
    symbols = identify_symbols(query)
    all_data = [d for s in symbols if (d := fetch_stock_data(s))]
    if not all_data: return "無法識別標的，請嘗試直接輸入『分析 2330 2317』。"

    prompt = f"""
    數據：{all_data}
    請扮演資深分析師，按以下格式對『每一支』進行專業診斷：
    
    1. 股票名稱(股票代號)
    分析結果：(依據 MA20、量價、RSI 撰寫專業判讀)
    
    最後給予橫向 PK 建議與推薦原因。繁體中文，禁廢話。
    """
    return call_gemini(prompt, use_pro=True)

def get_recommendations():
    # 推薦清單縮減至 5 支，確保不觸發 429 錯誤
    watchlist = ["2330.TW", "2317.TW", "3017.TW", "3324.TW", "2454.TW"]
    candidates = [d for s in watchlist if (d := fetch_stock_data(s))]
    
    prompt = f"請從以下 5 支標的中挑選 2 支技術籌碼皆優者，按 1. 2. 格式推薦並給予原因：{candidates}"
    return "💡 **今日 AI 嚴選推薦** 💡\n\n" + call_gemini(prompt, use_pro=False)

# --- LINE Webhook 處理 ---
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))
    line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))

    @handler.add(MessageEvent, message=TextMessage)
    def handle_message(event):
        user_text = event.message.text.strip()
        if any(w in user_text for w in ["你會什麼", "功能"]):
            reply_msg = "🤖 **AI 專家功能：**\n1.「分析 股票A 股票B」：專業 PK\n2.「推薦」：穩健選股"
        elif "推薦" in user_text:
            reply_msg = get_recommendations()
        elif "分析" in user_text:
            query = user_text.replace("分析", "").strip()
            reply_msg = analyze_and_compare(query)
        else: return
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

    try:
        handler.handle(body, signature)
    except InvalidSignatureError: abort(400)
    return 'OK'

@app.route("/")
def home(): return "節流優化版運作中"
