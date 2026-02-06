import os
import requests
import random
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# --- 全局狀態監控 ---
user_sessions = {}
usage_stats = {"finmind_count": 0, "last_reset": datetime.now()}

def get_api_status():
    """回報 FinMind API 本時段預估使用量"""
    now = datetime.now()
    if now - usage_stats["last_reset"] > timedelta(hours=1):
        usage_stats["finmind_count"] = 0
        usage_stats["last_reset"] = now
    return f"\n(📊 FinMind 本時段已用: {usage_stats['finmind_count']}/600)"

def call_gemini(prompt, use_pro=False):
    """具備 6 支金鑰輪替與 429 避險邏輯的 AI 呼叫器"""
    from google import genai
    api_keys = []
    for i in range(1, 7):
        key = os.environ.get(f'GEMINI_API_KEY_{i}')
        if key: api_keys.append((i, key))
    
    if not api_keys: api_keys = [(0, os.environ.get('GEMINI_API_KEY'))]
    
    # 隨機打亂順序以分散流量壓力
    random.shuffle(api_keys)
    target_model = "models/gemini-2.5-pro" if use_pro else "models/gemini-2.5-flash"
    
    for idx, selected_key in api_keys:
        try:
            client = genai.Client(api_key=selected_key)
            response = client.models.generate_content(model=target_model, contents=prompt)
            return response.text, f"Key_{idx}"
        except Exception as e:
            if "429" in str(e): continue # 滿額則換下一支
            return f"❌ 系統錯誤: {str(e)[:30]}", f"Key_{idx}_Err"
    
    return "🚀 所有金鑰皆達 RPM 上限，請等待 60 秒。", "All_Busy"

def identify_stocks(user_input):
    """精準提取股票名稱與代碼"""
    prompt = f"提取台股。格式『代碼:名稱』。只需回傳結果。"
    res, _ = call_gemini(prompt, use_pro=False)
    stock_map = {}
    try:
        for item in res.strip().split(','):
            parts = item.split(':')
            if len(parts) == 2: stock_map[parts[0].strip()] = parts[1].strip()
    except: pass
    return stock_map

def fetch_finmind_data(symbol):
    """使用 data_id 參數抓取價量與籌碼數據"""
    stock_id = symbol.split('.')[0]
    start_date = (datetime.now() - timedelta(days=50)).strftime('%Y-%m-%d')
    token = os.environ.get('FINMIND_TOKEN', '')
    
    try:
        usage_stats["finmind_count"] += 2
        # 修改為 data_id 確保上櫃股票讀取
        p_url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id={stock_id}&start_date={start_date}&token={token}"
        p_res = requests.get(p_url, timeout=8).json()
        
        c_url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&data_id={stock_id}&start_date={start_date}&token={token}"
        c_res = requests.get(c_url, timeout=8).json()
        
        if p_res.get('status') != 200: return "OVER"

        hist = [round(d['close'], 1) for d in p_res['data']][-35:]
        return {
            "id": symbol, "now": hist[-1], "ma20": round(sum(hist[-20:])/20, 1) if len(hist)>=20 else 0,
            "history": hist, "chips": c_res['data'][-12:] # 抓最近 4 日三大法人
        }
    except: return None

def analyze_and_compare(query, is_entry=False):
    stock_map = identify_stocks(query)
    all_data = []
    for sym, name in stock_map.items():
        data = fetch_finmind_data(sym)
        if data == "OVER": return "🚫 FinMind 額度已耗盡。"
        if data:
            data['name'] = name
            all_data.append(data)
    
    if not all_data: return "❌ 資料擷取失敗，請檢查代碼。"

    task = "提供具體買點、停利、停損建議" if is_entry else "深度診斷技術面與法人籌碼"
    
    # 嚴格約束格式，消除廢話
    prompt = f"""
    數據：{all_data}
    任務：{task}。
    【強制規範】：
    - 標題必須為：**股票名稱 (股票代號)**
    - 禁止問候、禁止開場白、禁止免責聲明。
    - 內容包含：MA20現況、法人買賣超診斷、短中線操作建議。
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
        
        # 入手價追問邏輯
        if any(k in u_text for k in ["入手", "買點", "進場"]) and u_id in user_sessions:
            msg = analyze_and_compare(user_sessions[u_id], is_entry=True)
        elif "推薦" in u_text:
            user_sessions[u_id] = "2303 3481 2409"
            msg = "🚀 **今日低基期推薦**：聯電(2303)、群創(3481)。\n(回覆「入手價」查看具體策略)"
        elif u_text.isdigit() or "分析" in u_text:
            query = u_text.replace("分析", "").strip()
            user_sessions[u_id] = query
            msg = analyze_and_compare(query)
        else: return

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))

    try: handler.handle(body, signature)
    except: abort(400)
    return 'OK'

@app.route("/")
def home(): return "FinMind + 6-Keys Professional Mode Active"
