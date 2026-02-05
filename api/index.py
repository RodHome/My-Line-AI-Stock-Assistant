import os, requests, random, re
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)
user_sessions = {} # 暫存最近查詢的代碼

def call_gemini(prompt, use_pro=True):
    from google import genai
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 5) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys: keys = [os.environ.get('GEMINI_API_KEY')]
    client = genai.Client(api_key=random.choice(keys))
    try:
        res = client.models.generate_content(model="gemini-2.5-pro" if use_pro else "gemini-2.5-flash", contents=prompt)
        return res.text
    except: return ""

# --- 強化版：先抓數字，再找名稱 ---
def get_stocks(text):
    stocks = {}
    # 1. 直接提取數字 (2330, 6683)
    nums = re.findall(r'\d{4,6}', text)
    for n in nums: stocks[n] = n 
    
    # 2. 如果包含中文，問 AI 名稱對應
    if re.search(r'[\u4e00-\u9fff]', text):
        prompt = f"將『{text}』轉為台股代碼。格式:代碼:名稱。例:6683:雍智科技,2330:台積電。只回傳代碼:名稱。"
        ai_raw = call_gemini(prompt, use_pro=False)
        matches = re.findall(r'(\d{4,6}):([^,\s，]+)', ai_raw)
        for code, name in matches: stocks[code] = name
    return stocks

def fetch_data(sid):
    # 同時嘗試 FinMind 與 Yahoo，確保不漏失
    token = os.environ.get('FINMIND_TOKEN', '')
    start = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')
    
    # --- 優先 FinMind ---
    try:
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&stock_id={sid}&start_date={start}&token={token}"
        p_data = requests.get(url, timeout=10).json().get('data', [])
        if p_data:
            prices = [d['close'] for d in p_data]
            v_url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&stock_id={sid}&start_date={start}&token={token}"
            c_data = requests.get(v_url, timeout=10).json().get('data', [])
            return {"prices": prices[-35:], "chips": c_data[-10:], "source": "FinMind"}
    except: pass

    # --- 備援 Yahoo ---
    try:
        y_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sid}.TW?range=2mo&interval=1d"
        y_res = requests.get(y_url, timeout=10).json()['chart']['result'][0]
        y_prices = [p for p in y_res['indicators']['quote'][0]['close'] if p]
        return {"prices": y_prices[-35:], "chips": [], "source": "Yahoo"}
    except:
        # 如果 .TW 不行，試試 .TWO (上櫃)
        try:
            y_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sid}.TWO?range=2mo&interval=1d"
            y_res = requests.get(y_url, timeout=10).json()['chart']['result'][0]
            y_prices = [p for p in y_res['indicators']['quote'][0]['close'] if p]
            return {"prices": y_prices[-35:], "chips": [], "source": "Yahoo(OTC)"}
        except: return None

def get_analysis(query, mode="normal"):
    stock_map = get_stocks(query)
    if not stock_map: return "❌ 識別不到股票名稱或代碼，請輸入如「分析 2330」或「雍智」。"
    
    results = []
    found_ids = []
    for sid, sname in stock_map.items():
        data = fetch_data(sid)
        if data:
            data['name'] = sname
            data['id'] = sid
            results.append(data)
            found_ids.append(sid)
    
    if not results: return f"❌ 已識別代碼 {list(stock_map.keys())}，但 FinMind 與 Yahoo 均無數據，請檢查 Token。"

    # 追問模式：強制關注入手價
    focus = "請特別針對【入手價建議、進場點位、分批佈局策略】進行分析" if mode == "price" else "進行技術、量價與籌碼分析"
    
    prompt = f"數據:{results}。任務:{focus}。格式:1.名稱(代號) 分析結果(含MA20、籌碼、點位)。繁體中文，禁廢話。"
    return call_gemini(prompt), " ".join(found_ids)

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
        
        # 追問邏輯：如果輸入包含「入手、買、價格」且先前有查詢紀錄
        if any(k in msg for k in ["入手", "買", "多少", "價格", "點位"]) and uid in user_sessions:
            ans, _ = get_analysis(user_sessions[uid], mode="price")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"針對『{user_sessions[uid]}』的入手分析：\n\n{ans}"))
        
        elif "推薦" in msg:
            user_sessions[uid] = "2303 3481 2409"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🚀 潛力股：聯電、群創、友達。可追問『入手價是多少？』"))
        
        elif re.search(r'\d{4,6}', msg) or len(msg) <= 5 or "分析" in msg:
            ans, last_ids = get_analysis(msg)
            user_sessions[uid] = last_ids # 存下這次查詢的代碼供追問
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=ans))
        
    try: handler.handle(body, signature)
    except: abort(400)
    return 'OK'

@app.route("/")
def home(): return "終極診斷版運作中"
