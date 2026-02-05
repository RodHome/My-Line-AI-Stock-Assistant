import os
import requests
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

def call_gemini(prompt, model_type="pro"):
    from google import genai
    client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))
    # 多股比對與專業分析強烈建議使用 Pro 版以維持邏輯完整
    target_model = "gemini-2.5-pro"
    try:
        response = client.models.generate_content(model=target_model, contents=prompt)
        return response.text
    except Exception as e:
        return f"AI 系統繁忙，請稍後再試。({str(e)})"

def identify_symbols(user_input):
    prompt = f"請將『{user_input}』中的所有股票轉為代碼(如 2330.TW, 3017.TW)。只回傳代碼並用逗號隔開，不要有其他文字。"
    result = call_gemini(prompt)
    return [s.strip() for s in result.split(',') if s.strip()]

def fetch_stock_data(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1d"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        data = res.json()
        result = data['chart']['result'][0]
        closes = [round(c, 2) for c in result['indicators']['quote'][0]['close'] if c is not None]
        return {"id": symbol, "current": closes[-1], "history": closes}
    except:
        return None

def analyze_and_compare(query):
    symbols = identify_symbols(query)
    all_data = [d for s in symbols if (d := fetch_stock_data(s))]
    
    if not all_data: return "找不到相關股票數據，請確認名稱是否正確。"

    # --- 核心 Prompt 優化：強制結構化輸出 ---
    prompt = f"""
    數據清單：{all_data}
    請扮演資深市場分析師，針對以上『每一支』股票進行獨立診斷與對比。
    
    回覆格式請『嚴格遵守』以下邏輯：
    
    1. 股票名稱(股票代號)
    分析結果：請從技術面(MA20趨勢)、量價關係、RSI強弱進行專業判讀。給予精闢的點評。
    
    2. 股票名稱(股票代號)
    分析結果：(同上，確保每一支都有獨立段落)
    
    最後，請針對上述比拚對象給予『投資建議』，明確指出哪一支較具優勢，並詳述原因。
    
    限制：
    - 繁體中文。
    - 禁止任何開場廢話。
    - 每一段分析要專業、精煉且具備標點符號。
    """
    return call_gemini(prompt)

def get_recommendations():
    watchlist = ["2330.TW", "2317.TW", "2454.TW", "3481.TW", "2409.TW", "2603.TW", "3037.TW"]
    candidates = [d for s in watchlist if (d := fetch_stock_data(s))]
    prompt = f"從以下數據挑出 2 支技術籌碼皆佳的標的，按上述1.2.格式回覆並給予建議：{candidates}"
    return "💡 **今日 AI 嚴選推薦**\n\n" + call_gemini(prompt)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))
    line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))

    @handler.add(MessageEvent, message=TextMessage)
    def handle_message(event):
        user_text = event.message.text.strip()
        
        if any(w in user_text for w in ["你會什麼", "功能", "技能"]):
            reply_msg = "🤖 **AI 股市專家功能：**\n1.「分析 股票A 股票B」：多股專業 PK 與對比\n2.「推薦」：篩選今日優質標的\n3. 支援代碼與中文名稱識別。"
        elif "推薦" in user_text:
            reply_msg = get_recommendations()
        elif "分析" in user_text:
            query = user_text.replace("分析", "").strip()
            reply_msg = analyze_and_compare(query)
        else:
            return # 忽略閒聊
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@app.route("/")
def home():
    return "專業 PK 助理運行中"
