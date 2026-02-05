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
    # 多股比對依然由 Pro 操刀，確保邏輯不打結
    target_model = "gemini-2.5-pro" if model_type == "pro" else "gemini-2.5-flash"
    try:
        response = client.models.generate_content(model=target_model, contents=prompt)
        return response.text
    except Exception as e:
        return f"AI 暫時離線：{str(e)}"

def identify_symbols(user_input):
    prompt = f"將『{user_input}』轉為台股或美股代碼(如 2330.TW, 2409.TW)。只回傳代碼並用逗號隔開。"
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
        return {"id": symbol, "current": closes[-1], "history": closes[-10:]}
    except:
        return None

def analyze_and_compare(query):
    symbols = identify_symbols(query)
    all_data = [d for s in symbols if (d := fetch_stock_data(s))]
    
    if not all_data: return "找不到相關股票數據，請確認名稱是否正確。"

    is_pk = len(all_data) > 1
    # 核心優化：強制格式化
    prompt = f"""
    數據清單：{all_data}
    請針對上述所有股票進行分析。
    
    格式要求：
    1. 每一支股票必須以『【股票名稱/代碼】』作為開頭。
    2. 診斷內容：1句話說明目前趨勢(MA20/量價)。
    3. 具體點位：給出『停利點』與『停損點』。
    { '4. PK 結論：最後用 1 句話總結誰最值得優先關注。' if is_pk else '' }
    
    限制：
    - 繁體中文，禁止任何開場廢話。
    - 內容要精煉，但『絕對必須』區分不同個股。
    """
    return call_gemini(prompt, model_type="pro" if is_pk else "flash")

def get_recommendations():
    watchlist = ["2330.TW", "2317.TW", "2454.TW", "3481.TW", "2409.TW", "2603.TW", "3037.TW"]
    candidates = [d for s in watchlist if (d := fetch_stock_data(s))]
    prompt = f"從以下數據挑出 2 支技術籌碼皆佳的標的，格式：【名稱】理由、停利/停損。字數200內：{candidates}"
    return "💡 **AI 今日優選推薦**\n\n" + call_gemini(prompt, model_type="flash")

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))
    line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))

    @handler.add(MessageEvent, message=TextMessage)
    def handle_message(event):
        user_text = event.message.text.strip()
        
        # 技能說明優化
        if any(w in user_text for w in ["你會什麼", "技能", "功能", "help"]):
            reply_msg = """🤖 **AI 股市大師 旗艦版**

📍 **核心技能：**
1. **多股/單股分析**：輸入「分析 奇鋐 雙鴻」，我會幫您進行橫向 PK 並標註每一支的狀態。
2. **智慧推薦**：輸入「推薦」，我會篩選出目前線型與籌碼穩健的標的。
3. **戰術指引**：分析報告將包含明確的「停利點」與「停損點」。
4. **全能搜尋**：支援台股、美股、中文名稱或代碼搜尋。

請問今天想看哪支股票？"""
        elif "推薦" in user_text:
            reply_msg = get_recommendations()
        elif "分析" in user_text:
            query = user_text.replace("分析", "").strip()
            reply_msg = analyze_and_compare(query)
        else:
            return
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@app.route("/")
def home():
    return "結構化分析助理運行中"
