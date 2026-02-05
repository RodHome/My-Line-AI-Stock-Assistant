import os
import requests
import random
import re
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)
user_sessions = {}

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
    except Exception: return "⚠️ AI 頻道暫時滿載。"

# --- 鋼鐵化識別：優先抓取數字，AI 僅用於輔助名稱查詢 ---
def get_stock_list(user_input):
    # 1. 先用正規表達式抓取所有 4-6 位的數字代碼 (如 2330, 6683, 0050)
    numbers = re.findall(r'\d{4,6}', user_input)
    stock_map = {n: n for n in numbers} # 預設名稱與代碼相同
    
    # 2. 如果包含中文，請 AI 幫忙轉換
    if re.search(r'[\u4e00-\u9fff]', user_input):
        prompt = f"將『{user_input}』中的股票轉為代碼。格式:代碼:名稱。例:2330:台積電,6683:雍智科技。只回傳結果。"
        ai_res = call_gemini(prompt)
        # 強化解析邏輯，避免 AI 廢話干擾
        items = re.findall(r'(\d{4,6}):([^,\s，]+)', ai_res)
        for code, name in items:
            stock_map[code] = name
            
    return stock_map

def fetch_finmind_data(stock_id):
    # FinMind 只需要純數字 ID
    start_date = (datetime.now() - timedelta(days=50)).strftime('%Y-%m-%d')
    token = os.environ.get('FINMIND_TOKEN', '')
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        # 抓取股價
        p_url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&stock_id={stock_id}&start_date={start_date}&token={token}"
        p_data = requests.get(p_url, headers=headers, timeout=10).json().get('data', [])
        
        # 抓取籌碼
        c_url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&stock_id={stock_id}&start_date={start_date}&token={token}"
        c_data = requests.get(c_url, headers=headers, timeout=10).json().get('data', [])
        
        if not p_data: return None
        
        prices = [round(d['close'], 1) for d in p_data]
        return {
            "id": stock_id,
            "current": prices[-1],
            "history": prices[-35:], # 確保足夠 35 筆分析 MA20
            "chips": [{"n": d['name'], "v": d['buy']-d['sell']} for d in c_data[-15:]]
        }
    except: return None

def analyze_and_compare(query, is_price=False):
    stock_map = get_stock_list(query)
    all_data = []
    for sid, sname in stock_map.items():
        data = fetch_finmind_data(sid)
        if data:
            data['name'] = sname
            all_data.append(data)
    
    if not all_data: return "❌ 找不到標的。請直接輸入代碼(如: 6683)或「分析 股票名」。"

    prompt = f"數據:{all_data}。任務:{'入手價建議' if is_price else '深度診斷'}。格式:1. 名稱(代號) 分析結果(含MA20、量價、法人籌碼、停損停利)。繁體中文，禁廢話。"
    return call_gemini(prompt, use_pro=True)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))
    line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))

    @handler.add(MessageEvent, message=TextMessage)
    def handle_message(event):
        uid = event.source.user_id
        msg = event.message.text.strip()
        
        # 1. 入手價追問
        if any(k in msg for k in ["入手", "買點", "多少"]) and uid in user_sessions:
            reply = analyze_and_compare(user_sessions[uid], is_price=True)
        # 2. 推薦
        elif "推薦" in msg:
            user_sessions[uid] = "2303 3481 2409"
            reply = "🚀 潛力股：聯電、群創、友達。可追問『入手價是多少？』"
        # 3. 技能
        elif "你會" in msg:
            reply = "🤖 功能：1.直接打代碼(6683) 2.分析 股票 3.推薦"
        # 4. 分析或直接輸入 (只要有數字或包含分析二字)
        elif "分析" in msg or re.search(r'\d{4,6}', msg) or len(msg) <= 4:
            user_sessions[uid] = msg
            reply = analyze_and_compare(msg)
        else:
            reply = "請輸入「分析 股票名」或「推薦」。"

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

    try: handler.handle(body, signature)
    except: abort(400)
    return 'OK'

@app.route("/")
def home(): return "2026 鋼鐵防護版運作中"
