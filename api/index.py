import os, requests, random, time
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)
user_sessions = {}

def call_gemini(prompt, model="flash"):
    """具備隨機延遲與 6 金鑰輪替的 AI 呼叫器"""
    from google import genai
    api_keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not api_keys: api_keys = [os.environ.get('GEMINI_API_KEY')]
    
    random.shuffle(api_keys)
    target = "models/gemini-2.0-flash" if model == "flash" else "models/gemini-2.5-pro"
    
    for key in api_keys:
        try:
            client = genai.Client(api_key=key)
            time.sleep(random.uniform(0.5, 1.2)) # 避開 Vercel IP 限流
            res = client.models.generate_content(model=target, contents=prompt)
            return res.text, "OK"
        except: continue
    return None, "Limit"

def get_stock_id(user_input):
    """將名稱或混雜文字轉換為標準 4 位代碼"""
    if user_input.isdigit() and len(user_input) >= 4: return user_input
    
    # 利用 Flash 進行極速轉換，Token 耗用極低
    prompt = f"將『{user_input}』轉換為台股代碼。只需回傳 4 位數字，若無法辨識回傳 None。"
    res, _ = call_gemini(prompt, model="flash")
    if res and res.strip().isdigit(): return res.strip()
    return None

def fetch_finmind_data(stock_id, mode="light"):
    """按需擷取：light 僅今日，full 包含 45 日與籌碼"""
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    days = 3 if mode == "light" else 45
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    
    try:
        # 價格數據
        p_res = requests.get(url, params={"dataset":"TaiwanStockPrice","data_id":stock_id,"start_date":start_date,"token":token}, timeout=8).json().get('data', [])
        if not p_res: return None
        
        if mode == "light": return {"close": p_res[-1]['close'], "name": stock_id}
        
        # 籌碼數據 (僅深度模式抓取)
        c_res = requests.get(url, params={"dataset":"TaiwanStockInstitutionalInvestorsBuySell","data_id":stock_id,"start_date":start_date,"token":token}, timeout=8).json().get('data', [])
        return {"history": p_res[-30:], "chips": c_res[-10:], "close": p_res[-1]['close']}
    except: return None

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))
    line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))

    @handler.add(MessageEvent, message=TextMessage)
    def handle_message(event):
        u_text = event.message.text.strip()
        
        # 1. 智慧路由：辨識代碼
        stock_id = get_stock_id(u_text)
        if not stock_id: return

        # 2. 決定模式：包含「分析」二字則進入重裝模式
        is_deep = any(k in u_text for k in ["分析", "詳細", "籌碼"])
        mode = "full" if is_deep else "light"
        
        data = fetch_finmind_data(stock_id, mode=mode)
        if not data:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 無法取得 {stock_id} 的資料。"))
            return

        # 3. 根據模式產出內容
        if is_deep:
            prompt = f"數據：{data}。任務：深度診斷籌碼與技術面。格式：**名稱 (代號)**，直接給策略，禁止贅詞。"
            ans, tag = call_gemini(prompt, model="pro")
        else:
            prompt = f"股票 {stock_id} 現價 {data['close']}。請給予一句話技術快評。"
            ans, tag = call_gemini(prompt, model="flash")

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"{ans}\n\n🏷️ 診斷: {tag}"))

    try: handler.handle(body, signature)
    except: abort(400)
    return 'OK'
