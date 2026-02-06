import os, requests, random, time
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)
user_sessions = {}

def call_gemini(prompt, model="flash"):
    from google import genai
    api_keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not api_keys: api_keys = [os.environ.get('GEMINI_API_KEY')]
    
    random.shuffle(api_keys)
    # 🎯 輕量快評用 2.0-Flash (限額高)，深度分析才用 2.5-Pro (限額低)
    target = "models/gemini-2.0-flash" if model == "flash" else "models/gemini-2.5-pro"
    
    for idx, key in enumerate(api_keys, 1):
        try:
            client = genai.Client(api_key=key)
            # 加入隨機擾動防止 IP 併發封鎖
            time.sleep(random.uniform(0.3, 0.8)) 
            res = client.models.generate_content(model=target, contents=prompt)
            if res.text: return res.text, f"K{idx}_{'F' if 'flash' in target else 'P'}"
        except: continue
    return "⚠️ 系統目前滿載，請於 30 秒後重試。", "Limit"

def get_stock_id(u_input):
    """識別代碼或名稱，轉換為 4 位代碼"""
    if u_input.isdigit() and len(u_input) >= 4: return u_input
    # 極簡 Prompt 節省 Token
    res, _ = call_gemini(f"Identify Taiwan stock ID for '{u_input}'. Reply only the 4-digit ID or 'None'.", model="flash")
    return res.strip() if res and res.strip().isdigit() else None

def fetch_finmind(stock_id, is_full=False):
    """根據需求強度抓取資料"""
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    # 深度模式抓 45 天，快評模式只抓 3 天
    days = 45 if is_full else 3
    start = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    
    try:
        p_res = requests.get(url, params={"dataset":"TaiwanStockPrice","data_id":stock_id,"start_date":start,"token":token}, timeout=8).json().get('data', [])
        if not p_res: return None
        if not is_full: return {"close": p_res[-1]['close'], "id": stock_id}
        
        # 僅深度模式才抓籌碼資料
        c_res = requests.get(url, params={"dataset":"TaiwanStockInstitutionalInvestorsBuySell","data_id":stock_id,"start_date":start,"token":token}, timeout=8).json().get('data', [])
        return {"history": p_res[-30:], "chips": c_res[-10:], "id": stock_id, "close": p_res[-1]['close']}
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
        
        # 1. 識別代碼
        stock_id = get_stock_id(u_text)
        if not stock_id: return

        # 2. 判斷模式 (是否有「分析」二字)
        is_deep = any(k in u_text for k in ["分析", "詳細", "籌碼", "推薦"])
        data = fetch_finmind(stock_id, is_full=is_deep)
        
        if not data:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 無法取得數據，請檢查代碼或稍後再試。"))
            return

        # 3. 分層 Prompt 分析
        if is_deep:
            prompt = f"數據：{data}。任務：深度診斷籌碼與技術面。格式：標題 **名稱 (代號)**，直接給策略，無廢話。"
            ans, tag = call_gemini(prompt, model="pro")
        else:
            # 極簡報價模式，消耗 Token 極低
            prompt = f"股票 {stock_id} 現價 {data['close']}。給予一句話極簡快評。"
            ans, tag = call_gemini(prompt, model="flash")

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"{ans}\n\n🏷️ 診斷: {tag}"))

    try: handler.handle(body, signature)
    except: abort(400)
    return 'OK'
