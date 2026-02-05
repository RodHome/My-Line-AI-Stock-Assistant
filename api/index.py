import os
import requests
import random
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

def call_gemini(prompt, use_pro=False):
    from google import genai
    api_keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 5) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not api_keys: api_keys = [os.environ.get('GEMINI_API_KEY')]
    
    selected_key = random.choice(api_keys)
    client = genai.Client(api_key=selected_key)
    target_model = "models/gemini-2.5-pro" if use_pro else "models/gemini-2.5-flash"
    
    try:
        response = client.models.generate_content(model=target_model, contents=prompt)
        return response.text
    except Exception as e:
        return "⚠️ 系統繁忙或流量上限，請稍後再試。"

# --- 修正 1：同時提取名稱與代碼 ---
def identify_stocks_with_names(user_input):
    prompt = f"""
    任務：從文字『{user_input}』中提取股票。
    要求：回傳格式必須為『代碼:名稱』，多支請用逗號隔開。
    範例：2330.TW:台積電, 3017.TW:奇鋐
    只需回傳結果，嚴禁廢話。
    """
    result = call_gemini(prompt, use_pro=False)
    stock_map = {}
    try:
        items = result.strip().split(',')
        for item in items:
            parts = item.split(':')
            if len(parts) == 2:
                stock_map[parts[0].strip()] = parts[1].strip()
    except: pass
    return stock_map

def fetch_stock_data(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1d"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(url, headers=headers, timeout=8)
        data = res.json()
        result = data['chart']['result'][0]
        closes = [round(c, 1) for c in result['indicators']['quote'][0]['close'] if c is not None]
        return {"id": symbol, "price": closes[-1], "history": closes[-15:]}
    except: return None

def analyze_and_compare(query):
    stock_map = identify_stocks_with_names(query)
    all_data = []
    for sym, name in stock_map.items():
        data = fetch_stock_data(sym)
        if data:
            data['name'] = name # 將名稱帶入數據包
            all_data.append(data)
    
    if not all_data: return "無法識別標的，請輸入正確名稱（如：分析 群創 聯電）。"

    prompt = f"""
    數據：{all_data}
    請扮演資深操盤手，針對以下每一支股票進行專業分析。
    
    回覆格式要求（極重要）：
    1. 股票名稱 (股票代號)
    分析結果：(請針對 MA20 趨勢、量價關係、RSI 進行專業診斷，並標註停利與停損點。)
    
    最後給予橫向 PK 建議。請用繁體中文，禁止開場白。
    """
    return call_gemini(prompt, use_pro=len(all_data) > 1)

# --- 修正 2：更新推薦名單與潛力股邏輯 ---
def get_recommendations():
    # 觀察清單改為低基期或轉折潛力股：聯電, 群創, 友達, 長榮航, 國泰金
    watchlist = ["2303.TW", "3481.TW", "2409.TW", "2618.TW", "2882.TW"]
    all_data = []
    # 這裡為了顯示名稱，手動對應
    names = {"2303.TW":"聯電", "3481.TW":"群創", "2409.TW":"友達", "2618.TW":"長榮航", "2882.TW":"國泰金"}
    
    for s in watchlist:
        data = fetch_stock_data(s)
        if data:
            data['name'] = names[s]
            all_data.append(data)
    
    prompt = f"""
    數據：{all_data}
    任務：從中挖掘 2 支『深蹲潛力股』。
    條件：避開已過度飆漲的高價股，優先選擇底部型態成形、量縮打底完成、或股價相對便宜具補漲潛力的標的。
    格式：1. 股票名稱 (股票代號) - 推薦理由。字數 250 內。
    """
    return "🚀 **今日 AI 潛力股挖掘 (低基期推薦)** 🚀\n\n" + call_gemini(prompt, use_pro=False)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))
    line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))

    @handler.add(MessageEvent, message=TextMessage)
    def handle_message(event):
        user_text = event.message.text.strip()
        if any(w in user_text for w in ["你會什麼", "技能"]):
            reply_msg = "🤖 **AI 股市專家：**\n1.「分析 股票」：專業 PK (含名稱)\n2.「推薦」：挖掘深蹲潛力股"
        elif "推薦" in user_text:
            reply_msg = get_recommendations()
        elif "分析" in user_text or user_text.isdigit():
            query = user_text.replace("分析", "").strip()
            reply_msg = analyze_and_compare(query)
        else: return
        
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

    try: handler.handle(body, signature)
    except InvalidSignatureError: abort(400)
    return 'OK'

@app.route("/")
def home(): return "2026 專業分析助理已上線"
