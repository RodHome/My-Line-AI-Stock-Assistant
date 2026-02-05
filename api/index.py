import os
import requests
import random
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# --- 核心 1：多金鑰自動輪替系統 (Load Balancing) ---
def call_gemini(prompt, use_pro=False):
    from google import genai
    
    # 收集環境變數中的所有金鑰 (支援 GEMINI_API_KEY_1 到 _4)
    api_keys = []
    for i in range(1, 5):
        key = os.environ.get(f'GEMINI_API_KEY_{i}')
        if key: api_keys.append(key)
    
    # 備援：若無編號金鑰，則使用原始金鑰
    if not api_keys:
        api_keys = [os.environ.get('GEMINI_API_KEY')]
    
    # 隨機選擇一組金鑰以分散流量壓力
    selected_key = random.choice(api_keys)
    client = genai.Client(api_key=selected_key)
    
    # 設定模型：多股PK用 Pro，其餘用 Flash 節省配額
    target_model = "models/gemini-2.5-pro" if use_pro else "models/gemini-2.5-flash"
    
    try:
        response = client.models.generate_content(model=target_model, contents=prompt)
        return response.text
    except Exception as e:
        if "429" in str(e):
            return "⚠️ 目前查詢人數較多導致流量過載，請稍等 30 秒後再試。"
        return f"AI 系統繁忙，請稍後再試。"

# --- 核心 2：精準股票代碼提取 ---
def identify_symbols(user_input):
    prompt = f"請從文字『{user_input}』中提取股票名並轉為台股代碼(如 2330.TW)。只需回傳代碼並用逗號隔開，嚴禁任何解釋文字。"
    result = call_gemini(prompt, use_pro=False)
    # 清洗掉可能出現的 Markdown 或空格
    clean_result = "".join(c for c in result if c.isalnum() or c in ".,")
    return [s.strip() for s in clean_result.split(',') if s.strip()]

# --- 核心 3：抓取 Yahoo Finance 數據 ---
def fetch_stock_data(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1d"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(url, headers=headers, timeout=8)
        data = res.json()
        result = data['chart']['result'][0]
        # 取得近一個月的收盤價
        closes = [round(c, 1) for c in result['indicators']['quote'][0]['close'] if c is not None]
        return {"id": symbol, "price": closes[-1], "history": closes[-15:]}
    except:
        return None

# --- 核心 4：多股 PK 與專業診斷邏輯 ---
def analyze_and_compare(query):
    symbols = identify_symbols(query)
    all_data = [d for s in symbols if (d := fetch_stock_data(s))]
    
    if not all_data:
        return "抱歉，無法識別您輸入的股票。請輸入如『分析 奇鋐 雙鴻』或單純輸入『2330』。"

    is_pk = len(all_data) > 1
    prompt = f"""
    數據清單：{all_data}
    請扮演專業市場分析師，依據數據針對每一支股票進行專業判讀。
    
    回覆格式要求：
    1. 股票名稱(股票代號)
    分析結果：(請針對 MA20 趨勢、量價關係、RSI 強弱進行精闢點評，並給出具體的『停利』與『停損』建議。)
    
    2. 股票名稱(股票代號)
    分析結果：(同上，確保每一支均有獨立編號與診斷。)
    
    最後，針對以上比拚對象給予『投資建議』，明確指出優選標的並詳述原因。
    
    限制：繁體中文，內容專業精煉，禁止開場廢話。
    """
    return call_gemini(prompt, use_pro=is_pk)

# --- 核心 5：今日選股推薦 ---
def get_recommendations():
    # 預設觀察名單
    watchlist = ["2330.TW", "2317.TW", "3017.TW", "3324.TW", "2454.TW"]
    candidates = [d for s in watchlist if (d := fetch_stock_data(s))]
    
    prompt = f"從以下數據挑選 2 支技術籌碼皆優者，按 1. 2. 格式推薦並詳述原因：{candidates}"
    return "💡 **今日 AI 嚴選推薦** 💡\n\n" + call_gemini(prompt, use_pro=False)

# --- LINE Webhook 主路由 ---
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Signature') # 修正為 X-Line-Signature（若套件需求）
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))
    line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))

    @handler.add(MessageEvent, message=TextMessage)
    def handle_message(event):
        user_text = event.message.text.strip()
        
        # 1. 技能介紹
        if any(w in user_text for w in ["你會什麼", "技能", "功能", "help"]):
            reply_msg = """🤖 **AI 股市專家功能清單：**
            
1. **多股 PK**：輸入「分析 奇鋐 雙鴻」。
2. **快速診斷**：直接輸入股票代碼「2330」。
3. **智慧推薦**：直接輸入「推薦」。
4. **格式規範**：包含專業趨勢判讀與停利停損點。"""

        # 2. 推薦功能
        elif "推薦" in user_text:
            reply_msg = get_recommendations()
        
        # 3. 分析功能 (包含關鍵字或純數字偵測)
        elif "分析" in user_text or user_text.isdigit():
            query = user_text.replace("分析", "").strip()
            reply_msg = analyze_and_compare(query)
            
        # 4. Fallback：避免已讀不回
        else:
            reply_msg = "您好！請輸入「分析 + 股票名稱」或直接輸入「代碼(如: 2330)」進行診斷。"
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@app.route("/")
def home():
    return "Gemini 2.5 旗艦版助理運作中"
