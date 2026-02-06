import os
import requests
import random
import time
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# --- 狀態監控與記憶 ---
user_sessions = {}
usage_stats = {"finmind_count": 0, "last_reset": datetime.now()}

def get_api_status():
    """回報 FinMind API 剩餘額度"""
    now = datetime.now()
    if now - usage_stats["last_reset"] > timedelta(hours=1):
        usage_stats["finmind_count"] = 0
        usage_stats["last_reset"] = now
    return f"\n(📊 FinMind 本時段已用: {usage_stats['finmind_count']}/600)"

def call_gemini(prompt, use_pro=False):
    """智慧輪替 6 支金鑰，Pro 滿載時自動切換 Flash"""
    from google import genai
    api_keys = []
    for i in range(1, 7):
        k = os.environ.get(f'GEMINI_API_KEY_{i}')
        if k: api_keys.append((i, k))
    
    if not api_keys: api_keys = [(0, os.environ.get('GEMINI_API_KEY'))]
    random.shuffle(api_keys)
    
    # 優先嘗試 Pro，若失敗則嘗試 Flash
    models_to_try = ["models/gemini-2.5-pro", "models/gemini-2.5-flash"] if use_pro else ["models/gemini-2.5-flash"]
    
    for model_path in models_to_try:
        for idx, key in api_keys:
            try:
                client = genai.Client(api_key=key)
                res = client.models.generate_content(model=model_path, contents=prompt)
                tag = "Pro" if "pro" in model_path else "Flash"
                return res.text, f"Key_{idx}({tag})"
            except Exception as e:
                if "429" in str(e): continue
                return f"⚠️ 系統錯誤: {str(e)[:20]}", f"Key_{idx}_Err"
    
    return "🚀 所有金鑰暫時滿載，請 30 秒後再試。", "All_Busy"

def fetch_finmind_data(symbol):
    """根據官方文件修正為 data_id 參數"""
    stock_id = symbol.split('.')[0]
    start_date = (datetime.now() - timedelta(days=50)).strftime('%Y-%m-%d')
    token = os.environ.get('FINMIND_TOKEN', '')
    
    try:
        usage_stats["finmind_count"] += 2
        # 核心修正：dataset=TaiwanStockPrice & data_id
        p_url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id={stock_id}&start_date={start_date}&token={token}"
        p_res = requests.get(p_url, timeout=10).json()
        
        c_url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id={stock_id}&start_date={start_date}&token={token}"
        c_res = requests.get(c_url, timeout=10).json()
        
        if p_res.get('status') != 200 or not p_res.get('data'): return None

        hist = [round(d['close'], 1) for d in p_res['data']][-35:]
        return {
            "id": symbol, "now": hist[-1], "ma20": round(sum(hist[-20:])/20, 1),
            "history": hist, "chips": c_res['data'][-10:]
        }
    except: return None

def analyze_and_compare(query, is_entry=False):
    # 先提取代碼
    id_prompt = "提取台股。格式『代碼:名稱』。只需回傳結果。"
    id_res, _ = call_gemini(id_prompt, use_pro=False)
    
    stock_map = {}
    try:
        for item in id_res.strip().split(','):
            parts = item.split(':')
            if len(parts) == 2: stock_map[parts[0].strip()] = parts[1].strip()
    except: pass

    all_data = []
    for sym, name in stock_map.items():
        if data := fetch_finmind_data(sym):
            data['name'] = name
            all_data.append(data)
    
    if not all_data: return "❌ 資料擷取失敗，請檢查代碼或稍後再試。"

    # 極簡分析 Prompt
    task = "買點與策略建議" if is_entry else "技術面與籌碼診斷"
    prompt = f"""
    數據：{all_data}
    任務：{task}。
    【規範】：
    - 標題：**股票名稱 (股票代號)**
    - 禁止任何問候語或免責聲明。直接給出 MA20 現況、法人趨勢與具體操作建議。
    """
    result, key_tag = call_gemini(prompt, use_pro=True)
    return f"{result}\n\n🏷️ 系統註記: {key_tag}{get_api_status()}"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))
    line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))

    @handler.add(MessageEvent, message=TextMessage)
    def handle_message(event):
        u_id = event.source.user_id
        u_text = event.message.text.strip()
        
        if any(k in u_text for k in ["入手", "買點"]) and u_id in user_sessions:
            msg = analyze_and_compare(user_sessions[u_id], is_entry=True)
        elif u_text.isdigit() or "分析" in u_text:
            user_sessions[u_id] = u_text.replace("分析", "").strip()
            msg = analyze_and_compare(user_sessions[u_id])
        elif "推薦" in u_text:
            msg = "🚀 潛力股：聯電(2303)、雍智(6683)。可詢問入手價。"
        else: return

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))

    try: handler.handle(body, signature)
    except: abort(400)
    return 'OK'

@app.route("/")
def home(): return "6-Key & FinMind data_id v4 Active"
