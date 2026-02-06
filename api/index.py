import os, requests, random, time, re
import json
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# 🟢 [版本號] v3.0 專業數據版 (法人+均線)
BOT_VERSION = "v3.0 (Pro Data)"

# --- 1. 快取名單 (包含熱門股與您提到的群創) ---
STOCK_CACHE = {
    "台積電": "2330", "tsmc": "2330", "鴻海": "2317", "聯發科": "2454",
    "長榮": "2603", "陽明": "2609", "萬海": "2615",
    "廣達": "2382", "緯創": "3231", "技嘉": "2376", "廣明": "6188",
    "鈊象": "3293", "智原": "3035", "創意": "3443", "世芯": "3661",
    "星宇": "2646", "星宇航空": "2646", "群創": "3481", "友達": "2409"
}

line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

# --- AI 呼叫模組 (使用穩定的 HTTP 請求) ---
def call_gemini_pro(prompt):
    # 讀取金鑰池
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys and os.environ.get('GEMINI_API_KEY'): keys = [os.environ.get('GEMINI_API_KEY')]
    random.shuffle(keys)
    
    # 鎖定 2.5 系列模型
    target_models = ["gemini-2.5-flash", "gemini-flash-latest"]

    for model in target_models:
        for key in keys:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                headers = {'Content-Type': 'application/json'}
                params = {'key': key}
                # 💡 增加回應長度 (350 tokens) 以容納詳細分析
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": 350, "temperature": 0.4}
                }
                time.sleep(random.uniform(0.5, 1.0))
                response = requests.post(url, headers=headers, params=params, json=payload, timeout=8)
                
                if response.status_code == 200:
                    data = response.json()
                    text = data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                    if text: return text.strip(), "Active"
            except: continue
    return None, "Fail"

# --- 核心：全方位數據抓取 (價格、均線、法人) ---
def fetch_comprehensive_data(stock_id):
    token = os.environ.get('FINMIND_TOKEN', '')
    base_url = "https://api.finmindtrade.com/api/v4/data"
    
    # 設定日期：抓取過去 45 天 (確保有足夠交易日算月線)
    start_date = (datetime.now() - timedelta(days=45)).strftime('%Y-%m-%d')
    
    result = {"price": 0, "ma5": None, "ma20": None, "foreign": 0, "trust": 0}
    
    try:
        # 1. 抓股價歷史 (用來算均線)
        p_res = requests.get(base_url, params={
            "dataset": "TaiwanStockPrice", "data_id": stock_id, "start_date": start_date, "token": token
        }, timeout=6)
        p_data = p_res.json().get('data', [])
        
        if not p_data: return None
        
        # 取得最新收盤價
        latest = p_data[-1]
        result['price'] = latest['close']
        
        # 計算均線 (MA5, MA20)
        closes = [d['close'] for d in p_data]
        if len(closes) >= 5: result['ma5'] = round(sum(closes[-5:]) / 5, 2)
        if len(closes) >= 20: result['ma20'] = round(sum(closes[-20:]) / 20, 2)

        # 2. 抓法人買賣超 (Foreign=外資, InvestmentTrust=投信)
        i_res = requests.get(base_url, params={
            "dataset": "TaiwanStockInstitutionalInvestorsBuySell", "data_id": stock_id, "start_date": start_date, "token": token
        }, timeout=6)
        i_data = i_res.json().get('data', [])
        
        if i_data:
            # 篩選最新一天的數據
            last_date = i_data[-1]['date']
            today_chips = [x for x in i_data if x['date'] == last_date]
            for chip in today_chips:
                name = chip.get('name', '')
                buy = chip.get('buy', 0) or 0
                sell = chip.get('sell', 0) or 0
                net = (buy - sell) // 1000 # 換算成張數
                
                if "Foreign" in name: result['foreign'] += net
                elif "InvestmentTrust" in name: result['trust'] += net
                
    except Exception as e:
        print(f"Data Error: {e}")
        return None

    return result

def get_stock_id(u_input):
    # 1. 查快取
    if u_input in STOCK_CACHE: return STOCK_CACHE[u_input]
    
    # 2. 濾網機制：如果是數字，必須是 4 位數才放行
    if u_input.isdigit():
        if len(u_input) == 4: return u_input
        return None 
    
    # 排除常見問候語，避免誤判
    if any(x in u_input for x in ["功能", "你好", "嗨", "哈囉"]): return None

    # 3. 查 AI (Regex 提取模式)
    prompt = f"Find the 4-digit stock code for Taiwan stock '{u_input}'. Answer ONLY the 4 digits (e.g. 2330)."
    res, _ = call_gemini_pro(prompt)
    
    if res:
        match = re.search(r'\d{4}', res)
        if match:
            code = match.group(0)
            STOCK_CACHE[u_input] = code
            return code
            
    return None

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except: abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    u_text = event.message.text.strip()
    
    # 🌟 功能說明指令
    if "功能" in u_text or "說明" in u_text:
        msg = (
            f"🤖 **Stock AI 助理 {BOT_VERSION}**\n"
            "------------------\n"
            "✅ **查詢個股**：直接輸入名稱 (如: 群創) 或代號 (3481)\n"
            "✅ **法人籌碼**：自動顯示外資與投信買賣超\n"
            "✅ **均線分析**：提供 MA5 週線與 MA20 月線數據\n"
            "✅ **AI 投顧**：綜合上述數據給予操作建議"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        return

    # A. 辨識
    stock_id = get_stock_id(u_text)
    if not stock_id:
        # 找不到代號時不回覆，避免干擾聊天
        return

    # B. 抓取完整數據
    data = fetch_comprehensive_data(stock_id)
    if not data:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 無法取得 {stock_id} 數據 (可能為新股或資料庫異常)"))
        return

    # C. AI 深度分析
    # 準備數據給 AI
    ma_info = ""
    if data['ma20']:
        status_ma = "站上" if data['price'] > data['ma20'] else "跌破"
        ma_info = f"MA20月線 {data['ma20']} ({status_ma})"
    else:
        ma_info = "MA20 資料不足"

    prompt = (
        f"你是專業股市分析師。分析股票 {stock_id}。\n"
        f"【數據】\n"
        f"- 收盤價: {data['price']}\n"
        f"- MA5週線: {data['ma5']}\n"
        f"- {ma_info}\n"
        f"- 外資動向: {data['foreign']} 張 (正數買超/負數賣超)\n"
        f"- 投信動向: {data['trust']} 張\n\n"
        f"【任務】\n"
        f"請用繁體中文，針對「均線乖離」與「法人籌碼」進行 80 字以內的技術短評。\n"
        f"請直接給出操作建議（例如：籌碼集中建議續抱、外資調節需保守）。"
    )
    
    ai_ans, status = call_gemini_pro(prompt)
    comment = ai_ans if ai_ans else "💡 AI 連線忙碌，請參考上方數據自行判斷。"
    
    # D. 組合回覆
    reply = (
        f"📊 **{stock_id} 專業分析**\n"
        f"💰 收盤: {data['price']}\n"
        f"📈 MA5 : {data['ma5']}\n"
        f"📉 MA20: {data['ma20']}\n"
        f"🏦 外資: {data['foreign']} 張\n"
        f"🏢 投信: {data['trust']} 張\n"
        f"------------------\n"
        f"🤖 **AI 投顧**:\n{comment}\n"
        f"(系統: {status} | {BOT_VERSION})"
    )
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
