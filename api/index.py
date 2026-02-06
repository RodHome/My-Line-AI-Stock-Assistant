import os, requests, random, time, re
import json
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# 🟢 [版本號] v3.3 (修正投信 0 張 bug)
BOT_VERSION = "v3.3 (Fix Trust)"

# --- 1. 快取名單 ---
STOCK_CACHE = {
    "台積電": "2330", "tsmc": "2330", "鴻海": "2317", "聯發科": "2454",
    "長榮": "2603", "陽明": "2609", "萬海": "2615",
    "廣達": "2382", "緯創": "3231", "技嘉": "2376", "廣明": "6188",
    "鈊象": "3293", "智原": "3035", "創意": "3443", "世芯": "3661",
    "星宇": "2646", "星宇航空": "2646", "群創": "3481", "友達": "2409",
    "華邦電": "2344", "華邦": "2344"
}

line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

# --- AI 核心 ---
def call_gemini_pro(prompt):
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys and os.environ.get('GEMINI_API_KEY'): keys = [os.environ.get('GEMINI_API_KEY')]
    random.shuffle(keys)
    
    target_models = ["gemini-2.5-flash", "gemini-flash-latest"]

    for model in target_models:
        for key in keys:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                headers = {'Content-Type': 'application/json'}
                params = {'key': key}
                # 參數維持 v3.2 的穩定設定
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": 800, "temperature": 0.3}
                }
                time.sleep(random.uniform(0.5, 1.0))
                response = requests.post(url, headers=headers, params=params, json=payload, timeout=12)
                
                if response.status_code == 200:
                    data = response.json()
                    text = data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                    if text: return text.strip(), "Active"
            except: continue
    return None, "Fail"

# --- 數據抓取 (修正判定邏輯) ---
def fetch_comprehensive_data(stock_id):
    token = os.environ.get('FINMIND_TOKEN', '')
    base_url = "https://api.finmindtrade.com/api/v4/data"
    start_date = (datetime.now() - timedelta(days=45)).strftime('%Y-%m-%d')
    result = {"price": 0, "ma5": None, "ma20": None, "foreign": 0, "trust": 0}
    
    try:
        # 1. 股價
        p_res = requests.get(base_url, params={
            "dataset": "TaiwanStockPrice", "data_id": stock_id, "start_date": start_date, "token": token
        }, timeout=6)
        p_data = p_res.json().get('data', [])
        
        if not p_data: return None
        result['price'] = p_data[-1]['close']
        
        closes = [d['close'] for d in p_data]
        if len(closes) >= 5: result['ma5'] = round(sum(closes[-5:]) / 5, 2)
        if len(closes) >= 20: result['ma20'] = round(sum(closes[-20:]) / 20, 2)

        # 2. 法人 (修正重點)
        i_res = requests.get(base_url, params={
            "dataset": "TaiwanStockInstitutionalInvestorsBuySell", "data_id": stock_id, "start_date": start_date, "token": token
        }, timeout=6)
        i_data = i_res.json().get('data', [])
        
        if i_data:
            last_date = i_data[-1]['date']
            today_chips = [x for x in i_data if x['date'] == last_date]
            
            for chip in today_chips:
                name = chip.get('name', '')
                buy = chip.get('buy', 0) or 0
                sell = chip.get('sell', 0) or 0
                net = (buy - sell) // 1000
                
                # 💡 修正：把 InvestmentTrust 改為 Trust，避開底線問題
                if "Foreign" in name: 
                    result['foreign'] += net
                elif "Trust" in name: 
                    result['trust'] += net
                    
    except: return None

    return result

def get_stock_id(u_input):
    if u_input in STOCK_CACHE: return STOCK_CACHE[u_input]
    if u_input.isdigit() and len(u_input) == 4: return u_input
    if any(x in u_input for x in ["功能", "你好", "嗨"]): return None

    prompt = f"Find 4-digit stock code for Taiwan stock '{u_input}'. Answer ONLY digits."
    res, _ = call_gemini_pro(prompt)
    if res:
        match = re.search(r'\d{4}', res)
        if match:
            STOCK_CACHE[u_input] = match.group(0)
            return match.group(0)
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
    
    if "功能" in u_text:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🤖 **Stock AI {BOT_VERSION}**\n請輸入股票名稱或代號查詢。"))
        return

    stock_id = get_stock_id(u_text)
    if not stock_id: return

    data = fetch_comprehensive_data(stock_id)
    if not data:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 數據異常 {stock_id}"))
        return

    ma_status = "站上" if data['ma20'] and data['price'] > data['ma20'] else "跌破"
    ma_str = f"MA20 {data['ma20']} ({ma_status})" if data['ma20'] else "MA20 無資料"
    
    # 提示詞 (保持 v3.2 的極速版設定)
    prompt = (
        f"你是一位嚴格的股市操盤手。分析 {stock_id}。\n"
        f"數據: 收盤{data['price']}, MA5 {data['ma5']}, {ma_str}, "
        f"外資{data['foreign']}張, 投信{data['trust']}張。\n"
        f"【嚴格規定】\n"
        f"1. 不需要打招呼，不需要標題。\n"
        f"2. 直接從「股價...」或「籌碼...」開始講。\n"
        f"3. 繁體中文，80字以內，給出多空建議。"
    )
    
    ai_ans, status = call_gemini_pro(prompt)
    comment = ai_ans if ai_ans else "💡 AI 思考超時，請參考上方數據。"
    
    reply = (
        f"📊 **{stock_id} 專業分析**\n"
        f"💰 收盤: {data['price']}\n"
        f"📈 MA5 : {data['ma5']}\n"
        f"📉 MA20: {data['ma20']}\n"
        f"🏦 外資: {data['foreign']} 張\n"
        f"🏢 投信: {data['trust']} 張\n"
        f"------------------\n"
        f"🤖 {comment}\n"
        f"(系統: {status} | {BOT_VERSION})"
    )
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
