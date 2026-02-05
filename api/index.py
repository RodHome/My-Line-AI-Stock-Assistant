import os
import requests
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

def call_gemini(prompt):
    from google import genai
    client = genai.Client(api_key=os.environ.get('GEMINI_API_KEY'))
    # 強制使用 Pro 模型處理複雜的多股邏輯
    try:
        response = client.models.generate_content(model="gemini-2.5-pro", contents=prompt)
        return response.text
    except Exception as e:
        return f"AI 系統繁忙，請稍後再試。"

def identify_symbols(user_input):
    # 強化版辨識 Prompt：要求 AI 從對話中剔除贅字
    prompt = f"請從以下文字『{user_input}』中提取所有提到的股票，並轉換成台股代碼(如 2330.TW, 3324.TWO)。請排除掉分析、比較等動詞。只回傳代碼，用逗號隔開。"
    result = call_gemini(prompt)
    return [s.strip() for s in result.split(',') if s.strip()]

def fetch_stock_data(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1d"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        data = res.json()
        result = data['chart']['result'][0]
        # 取得名稱（如果有）與收盤價
        closes = [round(c, 2) for c in result['indicators']['quote'][0]['close'] if c is not None]
        return {"id": symbol, "last": closes[-1], "history": closes}
    except:
        return None

def analyze_and_compare(query):
    # 1. 獲取代碼清單
    symbols = identify_symbols(query)
    all_data = []
    for s in symbols:
        data = fetch_stock_data(s)
        if data: all_data.append(data)
    
    if not all_data: 
        return "抱歉，無法從您的輸入中識別有效的股票名稱或代碼，請嘗試更換關鍵字（例如：分析 奇鋐 雙鴻）。"

    # 2. 建立專業分析 Prompt，嚴格要求 1. 2. 格式
    prompt = f"""
    數據來源：{all_data}
    
    請扮演『專業市場首席分析師』，針對數據清單中的每一支股票進行深度判讀。
    
    回覆內容請嚴格執行以下【固定格式】：
    
    1. 股票名稱(股票代號)
    分析結果：(請比照專業投顧報告，針對 MA20 趨勢、量價關係、K線型態進行深度解析，給予技術面與籌碼面的具體結論)。
    
    2. 股票名稱(股票代號)
    分析結果：(同上，確保每一支都有完整獨立的專業診斷)。
    
    最後，針對以上比拚對象給予『投資建議』，明確指出誰目前較具潛力或安全性，並詳細說明原因。
    
    限制：
    - 繁體中文，禁止任何招呼語或開場白。
    - 語氣必須嚴謹、專業。
    """
    return call_gemini(prompt)

def get_recommendations():
    watchlist = ["2330.TW", "2317.TW", "3017.TW", "3324.TW", "3481.TW", "2409.TW", "2603.TW"]
    candidates = [d for s in watchlist if (d := fetch_stock_data(s))]
    prompt = f"請從以下清單中，按 1. 2. 格式推薦兩支技術與籌碼皆優的股票：{candidates}"
    return "💡 **今日 AI 嚴選推薦** 💡\n\n" + call_gemini(prompt)

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
            reply_msg = "🤖 **AI 股市專家功能清單：**\n\n1.「分析 股票A 股票B」：多股專業 PK 與對比報告。\n2.「推薦」：篩選今日具備多頭型態之標的。\n3. 技術指標深度診斷 (MA、RSI、量價)。"
        elif "推薦" in user_text:
            reply_msg = get_recommendations()
        elif "分析" in user_text:
            # 取得「分析」後的所有文字，交給 AI 去篩選股票名
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
    return "2026 終極專業版運作中"
