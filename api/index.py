import os, requests, random, time, re
import json
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# 🟢 [版本號] v5.5 (字數解放: Token=2000 + 強制詳解)
BOT_VERSION = "v5.5 (Verbose)"

# --- 1. 快取名單 ---
STOCK_CACHE = {
    # 電子與權值
    "台積電": "2330", "tsmc": "2330", "鴻海": "2317", "聯發科": "2454",
    "廣達": "2382", "緯創": "3231", "技嘉": "2376", "台達電": "2308",
    "群創": "3481", "友達": "2409", "威剛": "3260", "中鋼": "2002",
    "興富發": "2542", "勤美": "1532", "長榮": "2603", "陽明": "2609", "萬海": "2615",
    "長榮航": "2618", "華航": "2610", 
    # 金融股
    "富邦金": "2881", "國泰金": "2882", "凱基金": "2883", "開發金": "2883",
    "玉山金": "2884", "元大金": "2885", "兆豐金": "2886", "台新金": "2887",
    "新光金": "2888", "永豐金": "2890", "中信金": "2891", "第一金": "2892",
    "合庫金": "5880", "華南金": "2880",
    # ETF
    "0050": "0050", "0056": "0056", "00878": "00878", "00929": "00929",
    "00919": "00919", "00940": "00940"
}

CODE_TO_NAME = {v: k for k, v in STOCK_CACHE.items()}

line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

@app.route("/")
def health_check():
    return "OK", 200

# --- AI 核心 ---
def call_gemini_v5_5(prompt, is_search=False):
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys and os.environ.get('GEMINI_API_KEY'):
        keys = [os.environ.get('GEMINI_API_KEY')]
    
    random.shuffle(keys)
    last_error = "NoKeys"
    
    # 🔥🔥🔥 關鍵修正：Token 拉到 2000 (絕對足夠寫 600 字以上) 🔥🔥🔥
    max_tokens = 100 if is_search else 2000
    
    target_models = ["gemini-2.5-flash", "gemini-2.0-flash-lite-001", "gemini-flash-latest"]

    for model in target_models:
        for key in keys:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                headers = {'Content-Type': 'application/json'}
                params = {'key': key}
                payload = {
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "maxOutputTokens": max_tokens, 
                        "temperature": 0.4 # 稍微調高溫度，讓 AI 更願意多話一點
                    }
                }
                
                time.sleep(random.uniform(0.3, 0.7))
                response = requests.post(url, headers=headers, params=params, json=payload, timeout=12) # 延長等待
                
                if response.status_code == 200:
                    data = response.json()
                    text = data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                    if text: return text.strip(), "Active"
                else:
                    last_error = f"{response.status_code}"
            except:
                last_error = "Err"
                continue
    return None, f"Fail({last_error})"

# --- 輔助函式 ---
def clean_input(text):
    return re.sub(r"(建議|分析|買進|策略|怎麼看|分析一下)\s*", "", text).strip()

def get_stock_id(u_input):
    clean_name = clean_input(u_input)
    if clean_name in STOCK_CACHE: return STOCK_CACHE[clean_name]
    if clean_name.isdigit() and len(clean_name) >= 4: return clean_name
    
    prompt = (
        f"Identify the 4-digit stock code for Taiwan stock '{clean_name}'. "
        f"Reply ONLY with the 4-digit number. If unsure, return nothing."
    )
    res, status = call_gemini_v5_5(prompt, is_search=True)
    if res:
        match = re.search(r'\d{4}', res)
        if match:
            code = match.group(0)
            STOCK_CACHE[clean_name] = code
            CODE_TO_NAME[code] = clean_name
            return code
    return None

def get_stock_name(stock_id, user_input_name=None):
    if stock_id in CODE_TO_NAME: return CODE_TO_NAME[stock_id]
    if user_input_name and not user_input_name.isdigit(): return user_input_name
    return ""

# --- 營收抓取 ---
def fetch_revenue(stock_id):
    if stock_id.startswith("00"): return "ETF無營收數據"

    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    start = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
    params = { "dataset": "TaiwanStockMonthRevenue", "data_id": stock_id, "start_date": start, "token": token }
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, params=params, headers=headers, timeout=12)
        
        if res.status_code == 429: return "API限速"
        if res.status_code != 200: return f"API錯誤"
            
        data = res.json().get('data', [])
        if data:
            latest = data[-1]
            return f"{latest['revenue_month']}月營收年增 {latest['revenue_year_growth_rate']}%"
        return "營收尚未更新"
    except:
        return "營收讀取逾時"

# --- 技術面 ---
def fetch_technical_data(stock_id):
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    start = (datetime.now() - timedelta(days=70)).strftime('%Y-%m-%d')
    params = { "dataset": "TaiwanStockPrice", "data_id": stock_id, "start_date": start, "token": token }
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, params=params, headers=headers, timeout=10)
        data = res.json().get('data', [])
        if not data: return None
        
        latest = data[-1]
        closes = [d['close'] for d in data]
        volumes = [d['Trading_Volume'] for d in data]
        
        ma5 = round(sum(closes[-5:]) / 5, 2) if len(closes) >= 5 else 0
        ma20 = round(sum(closes[-20:]) / 20, 2) if len(closes) >= 20 else 0
        
        if len(volumes) >= 6:
            vol_avg_5 = sum(volumes[-6:-1]) / 5
            vol_ratio = round(latest['Trading_Volume'] / vol_avg_5, 1) if vol_avg_5 > 0 else 0
        else:
            vol_ratio = 1.0

        return {
            "close": latest['close'],
            "volume": latest['Trading_Volume'],
            "ma5": ma5,
            "ma20": ma20,
            "vol_ratio": vol_ratio,
            "trend": "多頭" if latest['close'] > ma20 else "空頭"
        }
    except: return None

# --- 籌碼面 ---
def fetch_chips(stock_id):
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    start = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
    params = {"dataset": "TaiwanStockInstitutionalInvestorsBuySell", "data_id": stock_id, "start_date": start, "token": token}
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, params=params, headers=headers, timeout=10)
        data = res.json().get('data', [])
        if not data: return {"foreign": 0, "trust": 0}
        
        latest_date = data[-1]['date']
        chips = {"foreign": 0, "trust": 0}
        for row in reversed(data):
            if row['date'] != latest_date: break
            if row['name'] == 'Foreign_Investor': chips['foreign'] = row['buy'] - row['sell']
            elif row['name'] == 'Investment_Trust': chips['trust'] = row['buy'] - row['sell']
        return chips
    except: return {"foreign": 0, "trust": 0}

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

    if u_text.lower() == "debug":
        token = os.environ.get('FINMIND_TOKEN', '')
        token_status = f"✅ 已設定" if token else "❌ 未設定"
        ai_res, ai_status = call_gemini_v5_5("Hi", is_search=True)
        reply = f"🛠️ **系統診斷**\nVer: {BOT_VERSION}\nToken: {token_status}\nAI連線: {ai_status}"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    stock_id = get_stock_id(u_text)
    if not stock_id:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 找不到「{u_text}」"))
        return

    stock_name = get_stock_name(stock_id, u_text)
    display_name = f"{stock_id} {stock_name}".strip()

    tech = fetch_technical_data(stock_id)
    if not tech:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 無 {stock_id} 資料"))
        return

    chips = fetch_chips(stock_id)
    f_sheets = int(chips['foreign'] / 1000)
    t_sheets = int(chips['trust'] / 1000)
    revenue_info = fetch_revenue(stock_id)

    # 🔥🔥🔥 Prompt 調整：要求「詳盡分析」，避免字數過少 🔥🔥🔥
    prompt = (
        f"角色：資深台股分析師。\n"
        f"標的：{display_name}，現價 {tech['close']}。\n"
        f"【技術面】：\n"
        f"- 趨勢: {tech['trend']} (MA20: {tech['ma20']})\n"
        f"- 量能: 量比 {tech['vol_ratio']} 倍 (成交 {int(tech['volume']/1000)} 張)\n"
        f"【籌碼面】：外資 {f_sheets} 張，投信 {t_sheets} 張。\n"
        f"【基本面】：{revenue_info}。\n"
        f"任務：請撰寫一份【完整詳盡】的操盤建議，字數目標 200 字以上。\n"
        f"必須包含以下四點，且每一點都要有具體的解釋，不要只有一句話：\n\n"
        f"1. **量價結構分析**：(詳細解釋量比意義，配合均線判斷多空力道)\n"
        f"2. **法人籌碼解讀**：(分析外資與投信的意圖，是真買還是假拉)\n"
        f"3. **實戰操作建議**：(明確建議進場點、加碼點或觀望理由)\n"
        f"4. **風險與防守**：(設定具體停損價，並提醒隔日沖風險)\n\n"
        f"語氣要專業、犀利，多使用股市術語，並確保回答完整不斷氣。"
    )
    
    ai_ans, status = call_gemini_v5_5(prompt)
    
    reply = (
        f"📊 **{display_name} 深度分析**\n"
        f"💰 價: {tech['close']} | 量比: {tech['vol_ratio']}x\n"
        f"📈 月線: {tech['ma20']} ({tech['trend']})\n"
        f"🏦 外資: {f_sheets}張 | 投信: {t_sheets}張\n"
        f"📝 {revenue_info}\n"
        f"------------------\n"
        f"{ai_ans}\n"
        f"------------------\n"
        f"(AI分析師: {status})"
    )

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
