import os
import requests
import random
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# --- 全局記憶區 (Session) ---
user_sessions = {}

def call_gemini(prompt, use_pro=False):
    from google import genai
    # 多金鑰輪替邏輯 (維持不變)
    api_keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 5) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not api_keys: api_keys = [os.environ.get('GEMINI_API_KEY')]
    selected_key = random.choice(api_keys)
    client = genai.Client(api_key=selected_key)
    target_model = "models/gemini-2.5-pro" if use_pro else "models/gemini-2.5-flash"
    try:
        response = client.models.generate_content(model=target_model, contents=prompt)
        return response.text
    except Exception as e:
        return "⚠️ AI 頻道滿載，請稍候再試。"

def identify_stocks_with_names(user_input):
    prompt = f"從文字『{user_input}』提取台股。上市補.TW，上櫃(如6683)補.TWO。回傳格式『代碼:名稱』，多支用逗號隔開。只需回傳結果。"
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

# --- 核心：FinMind 數據抓取 (含籌碼) ---
def fetch_finmind_data(symbol):
    stock_id = symbol.split('.')[0]
    # 計算起始日期 (抓 50 天確保扣除假日仍有 35 筆以上)
    start_date = (datetime.now() - timedelta(days=50)).strftime('%Y-%m-%d')
    api_token = os.environ.get('FINMIND_TOKEN', '') # 建議在 Vercel 設定 Token
    
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        # 1. 抓取股價與成交量
        price_url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&stock_id={stock_id}&start_date={start_date}&token={api_token}"
        price_res = requests.get(price_url, headers=headers, timeout=8).json()
        price_data = price_res['data']
        
        # 2. 抓取法人買賣超 (籌碼)
        chip_url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&stock_id={stock_id}&start_date={start_date}&token={api_token}"
        chip_res = requests.get(chip_url, headers=headers, timeout=8).json()
        chip_data = chip_res['data']
        
        # 整理近 35 筆成交價與成交量
        history_prices = [round(d['close'], 1) for d in price_data][-35:]
        history_volumes = [d['Volume'] for d in price_data][-35:]
        
        # 整理近 5 日法人動態 (看短線籌碼集中度)
        recent_chips = []
        for d in chip_data[-15:]: # 抓最近 15 筆明細供 AI 綜合判斷
            recent_chips.append({
                "date": d['date'],
                "name": d['name'], # 外資、投信或自營商
                "net": d['buy'] - d['sell'] # 買賣超張數
            })
            
        return {
            "id": symbol,
            "current_price": history_prices[-1],
            "history_prices": history_prices,
            "history_volumes": history_volumes,
            "chips": recent_chips
        }
    except:
        return None

def analyze_and_compare(query, is_entry_price=False):
    stock_map = identify_stocks_with_names(query)
    all_data = []
    for sym, name in stock_map.items():
        data = fetch_finmind_data(sym)
        if data:
            data['name'] = name
            all_data.append(data)
    
    if not all_data: return "找不到標的，請嘗試直接輸入『分析 雍智』。"

    task = "提供具體入手價建議與防守策略" if is_entry_price else "進行技術與法人籌碼深度分析"
    
    prompt = f"""
    數據源：{all_data}
    請扮演『專業首席操盤手』，執行任務：{task}。
    
    格式規範：
    1. 股票名稱 (股票代號)
    分析結果：(請包含：1. MA20 趨勢 2. 價量結構 3. 法人籌碼動向(觀察外資與投信近5日買賣超數字) 4. 停利與停損點建議。)
    
    最後給予綜合投資建議。繁體中文，內容專業、精簡、禁廢話。
    """
    return call_gemini(prompt, use_pro=True)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))
    line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))

    @handler.add(MessageEvent, message=TextMessage)
    def handle_message(event):
        user_id = event.source.user_id
        user_text = event.message.text.strip()
        
        # 智慧追問邏輯
        entry_keywords = ["入手", "買點", "進場", "多少錢", "價格"]
        if any(k in user_text for k in entry_keywords) and user_id in user_sessions:
            last_query = user_sessions[user_id]
            reply_msg = analyze_and_compare(last_query, is_entry_price=True)
        elif "推薦" in user_text:
            user_sessions[user_id] = "聯電 友達 群創" 
            reply_msg = "🚀 AI 潛力股：聯電、友達。您可以接著追問『入手價是多少？』"
        elif "分析" in user_text or user_text.isdigit():
            query = user_text.replace("分析", "").strip()
            user_sessions[user_id] = query
            reply_msg = analyze_and_compare(query)
        else:
            reply_msg = "您可以輸入「分析 股票」或「推薦」進行挖掘。"

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))

    try: handler.handle(body, signature)
    except: abort(400)
    return 'OK'

@app.route("/")
def home(): return "FinMind 籌碼強化版運作中"
