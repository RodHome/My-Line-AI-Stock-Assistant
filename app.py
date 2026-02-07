import os, requests, random, time, re
import json
import concurrent.futures # 平行運算
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)

# 🟢 [版本號] v8.4 (Fix-Reply: Single-Response)
BOT_VERSION = "v8.4 (Turbo-Scan)"

# --- 1. 菁英股票池 (Top 150) ---
STOCK_CACHE = {
    # 半導體/電子
    "台積電": "2330", "聯發科": "2454", "鴻海": "2317", "台達電": "2308", "聯電": "2303", 
    "日月光": "3711", "廣達": "2382", "緯創": "3231", "智邦": "2345", "華碩": "2357",
    "研華": "2395", "瑞昱": "2379", "聯詠": "3034", "大立光": "3008", "光寶科": "2301",
    "和碩": "4938", "緯穎": "6669", "矽力": "6415", "南亞科": "2408", "友達": "2409",
    "群創": "3481", "微星": "2377", "技嘉": "2376", "英業達": "2356", "仁寶": "2324",
    "宏碁": "2353", "佳世達": "2352", "華邦電": "2344", "京元電": "2449", "力積電": "6770",
    "聯強": "2347", "大聯大": "3702", "文曄": "3036", "健鼎": "3044", "欣興": "3037",
    "南電": "8046", "景碩": "3189", "台光電": "2383", "台燿": "6274", "金像電": "2368",
    "奇鋐": "3017", "雙鴻": "3324", "建準": "2421", "力致": "3483", "愛普": "6531",
    "智原": "3035", "創意": "3443", "世芯": "3661", "M31": "6643", "祥碩": "5269",
    "嘉澤": "3533", "致茂": "2360", "義隆": "2458", "新唐": "4919", "威剛": "3260",
    "群聯": "8299", "十銓": "4967", "正隆": "1904", "山隆": "2616", "榮剛": "5009", "增你強": "2340",
    # 金融/傳產
    "富邦金": "2881", "國泰金": "2882", "中信金": "2891", "兆豐金": "2886", "玉山金": "2884",
    "元大金": "2885", "第一金": "2892", "合庫金": "5880", "華南金": "2880", "台新金": "2887",
    "永豐金": "2890", "凱基金": "2883", "彰銀": "2801", "臺企銀": "2834", "遠東銀": "2845",
    "台泥": "1101", "亞泥": "1102", "台塑": "1301", "南亞": "1303", "台化": "1326",
    "台塑化": "6505", "遠東新": "1402", "中鋼": "2002", "豐興": "2015", "大成鋼": "2027",
    "統一": "1216", "統一超": "2912", "和泰車": "2207", "裕隆": "2201", "巨大": "9921",
    "長榮": "2603", "陽明": "2609", "萬海": "2615", "長榮航": "2618", "華航": "2610",
    "慧洋": "2637", "裕民": "2606", "華城": "1519", "士電": "1503", "中興電": "1513",
    "東元": "1504", "亞力": "1514", "世紀鋼": "9958", "上緯": "3708"
}

CODE_TO_NAME = {v: k for k, v in STOCK_CACHE.items()}

# Token
token = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
secret = os.environ.get('LINE_CHANNEL_SECRET')
line_bot_api = LineBotApi(token if token else 'UNKNOWN')
handler = WebhookHandler(secret if secret else 'UNKNOWN')

@app.route("/")
def health_check():
    return "OK", 200

# --- AI 核心 ---
def call_gemini_v8_4(prompt, system_instruction=None):
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys and os.environ.get('GEMINI_API_KEY'):
        keys = [os.environ.get('GEMINI_API_KEY')]
    
    if not keys: return None, "NoKeys"
    random.shuffle(keys)
    max_tokens = 2000
    
    # 優先使用 2.0 Flash
    target_models = ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-flash-latest"]

    for model in target_models:
        for key in keys:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                headers = {'Content-Type': 'application/json'}
                params = {'key': key}
                contents = [{"parts": [{"text": prompt}]}]
                if system_instruction:
                    full_prompt = f"【系統指令】：{system_instruction}\n\n【用戶請求】：{prompt}"
                    contents = [{"parts": [{"text": full_prompt}]}]

                payload = {
                    "contents": contents,
                    "generationConfig": {
                        "maxOutputTokens": max_tokens, 
                        "temperature": 0.3
                    }
                }
                response = requests.post(url, headers=headers, params=params, json=payload, timeout=25)
                if response.status_code == 200:
                    data = response.json()
                    text = data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                    if text: return text.strip(), "Active"
                continue
            except: continue
    return "AI 忙碌中", "Timeout"

# --- 資料抓取 (輕量化) ---
def fetch_data_light(stock_id):
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        start = (datetime.now() - timedelta(days=100)).strftime('%Y-%m-%d')
        res = requests.get(url, params={"dataset": "TaiwanStockPrice", "data_id": stock_id, "start_date": start, "token": token}, headers=headers, timeout=5)
        data = res.json().get('data', [])
        if not data: return None
        latest = data[-1]
        closes = [d['close'] for d in data]
        ma20 = round(sum(closes[-20:]) / 20, 2) if len(closes) >= 20 else 0
        ma60 = round(sum(closes[-60:]) / 60, 2) if len(closes) >= 60 else 0
        return {"code": stock_id, "close": latest['close'], "ma20": ma20, "ma60": ma60}
    except: return None

def fetch_chips_quick(stock_id):
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        start = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
        res = requests.get(url, params={"dataset": "TaiwanStockInstitutionalInvestorsBuySell", "data_id": stock_id, "start_date": start, "token": token}, headers=headers, timeout=5)
        data = res.json().get('data', [])
        f, t = 0, 0
        if data:
            last_date = data[-1]['date']
            for row in reversed(data):
                if row['date'] != last_date: break
                if row['name'] == 'Foreign_Investor': f = row['buy'] - row['sell']
                elif row['name'] == 'Investment_Trust': t = row['buy'] - row['sell']
        return int(f/1000), int(t/1000)
    except: return 0, 0

# --- 完整資料抓取 ---
def fetch_full_data(stock_id):
    basic = fetch_data_light(stock_id)
    if not basic: return None
    f, t = fetch_chips_quick(stock_id)
    basic['foreign'] = f
    basic['trust'] = t
    return basic

def fetch_eps(stock_id):
    if stock_id.startswith("00"): return "ETF無EPS"
    token = os.environ.get('FINMIND_TOKEN', '')
    start = (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d')
    try:
        res = requests.get("https://api.finmindtrade.com/api/v4/data", params={"dataset": "TaiwanStockFinancialStatements", "data_id": stock_id, "start_date": start, "token": token}, timeout=5)
        data = res.json().get('data', [])
        if not data: return "EPS無資料"
        eps_data = [d for d in data if d['type'] == 'EPS']
        if not eps_data: return "EPS無資料"
        latest_year = eps_data[-1]['date'][:4]
        vals = [d['value'] for d in eps_data if d['date'].startswith(latest_year)]
        return f"{latest_year}累計{round(sum(vals), 2)}元"
    except: return "EPS逾時"

# --- 核心邏輯 ---
def get_stock_id(text):
    text = text.strip()
    if text in STOCK_CACHE: return STOCK_CACHE[text]
    if text.isdigit() and len(text) >= 4: return text
    if len(text) > 6 or "推薦" in text: return None
    prompt = f"Identify the 4-digit stock code for Taiwan stock '{text}'. Reply ONLY with the 4-digit number. If NOT stock, return nothing."
    res, _ = call_gemini_v8_4(prompt)
    if res and (match := re.search(r'\d{4}', res)):
        code = match.group(0)
        STOCK_CACHE[text] = code
        return code
    return None

# 🔥 平行掃描單一股票 (Worker Function)
def check_stock_worker(code):
    try:
        # 1. 抓股價
        data = fetch_data_light(code)
        if not data: return None
        
        # 2. 篩選：股價 > 月線 > 季線 (多頭排列)
        if data['close'] > data['ma20'] and data['ma20'] > data['ma60']:
            # 3. 只有通過才查籌碼 (省時間)
            f, t = fetch_chips_quick(code)
            if (f + t) > 0:
                name = CODE_TO_NAME.get(code, code)
                return f"{name}({code}): 價{data['close']} > 季線{data['ma60']}, 法人買{f+t}張"
    except:
        return None
    return None

# 🔥 Turbo 掃描引擎
def scan_recommendations_turbo():
    candidates = []
    # 隨機抽 25 檔 (數量增加)
    sample_list = random.sample(list(STOCK_CACHE.values()), 25)
    sample_list = [c for c in sample_list if not c.startswith("00")]
    
    # 啟動平行運算
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(check_stock_worker, sample_list)
    
    for res in results:
        if res: candidates.append(res)
        if len(candidates) >= 3: break
        
    return candidates

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except: abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg = event.message.text.strip()
    
    # --- A. 推薦功能 (Turbo) ---
    if msg in ["推薦", "選股", "有什麼好買的"]:
        # 🔥🔥🔥 修正：不先回覆 "Loading..."，直接掃描並回覆結果 🔥🔥🔥
        # 這樣就只有一次 reply_message，絕對不會報錯
        
        good_stocks = scan_recommendations_turbo()
        
        if not good_stocks:
            reply = "⚠️ 掃描了 25 檔菁英股，暫無發現「完美多頭(價>月>季+法人買)」標的。\n建議目前空手觀望，或稍後再試。"
        else:
            stocks_str = "\n".join(good_stocks)
            prompt = (
                f"你是專業操盤手。系統已篩選出強勢標的：\n{stocks_str}\n\n"
                f"任務：挑選最值得留意的 1-3 檔點評。\n"
                f"指令：\n"
                f"1. **禁止廢話**：直接開始分析。\n"
                f"2. **格式**：\n"
                f"   🔥 [股票名稱]\n"
                f"   [理由] (簡評)\n"
                f"   [價位] (支撐壓力)\n"
            )
            ai_ans, status = call_gemini_v8_4(prompt)
            reply = f"🎯 **AI 菁英快篩**\n(條件: 價>季線+法人買)\n------------------\n{ai_ans}\n------------------\n(系統: {status})"
            
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # --- B. 系統診斷 ---
    if msg.lower() == "debug":
        token_chk = os.environ.get('FINMIND_TOKEN', '')
        ai_res, ai_stat = call_gemini_v8_4("Hi")
        reply = f"🛠️ **v8.4 診斷**\nToken: {'✅' if token_chk else '❌'}\nAI: {ai_stat}\nMode: Parallel Scan"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # --- C. 判斷股票 ---
    stock_id = get_stock_id(msg)

    # --- D. 防呆引導 ---
    if not stock_id:
        reply = (
            "🤖 **功能選單**\n\n"
            "1. 🔍 **個股分析**：\n輸入「2330」、「鴻海」\n\n"
            "2. 🎯 **潛力推薦**：\n輸入「推薦」 (Turbo掃描)\n\n"
            "3. 🛠️ **系統診斷**：\n輸入「Debug」"
        )
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return

    # --- E. 個股分析 ---
    name = CODE_TO_NAME.get(stock_id, stock_id)
    data = fetch_full_data(stock_id)
    
    if not data:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 無法讀取 {stock_id} 數據"))
        return
        
    eps = fetch_eps(stock_id)

    sys_prompt = "你是一個冷酷的看盤機器，禁止打招呼，禁止自我介紹，直接輸出分析結果。語氣專業、犀利。"
    user_prompt = (
        f"標的：{stock_id} {name}\n"
        f"現價：{data['close']} (MA20={data['ma20']}, MA60={data['ma60']})\n"
        f"籌碼：外資{data['foreign']}張, 投信{data['trust']}張\n"
        f"EPS：{eps}\n\n"
        f"任務：請進行極簡分析，總字數 200 字內。\n"
        f"格式：\n"
        f"【趨勢】 (務必參考MA60季線判斷長線)\n"
        f"【籌碼】 (解讀法人動向)\n"
        f"【建議】 (操作策略與防守價)"
    )
    
    ai_ans, status = call_gemini_v8_4(user_prompt, system_instruction=sys_prompt)
    
    reply = (
        f"📊 **{name} {data['close']}**\n"
        f"MA20: {data['ma20']} | MA60: {data['ma60']}\n"
        f"外資: {data['foreign']} | 投信: {data['trust']}\n"
        f"💎 {eps}\n"
        f"------------------\n"
        f"{ai_ans}\n"
        f"------------------\n"
        f"(系統: {status} | v8.4)"
    )

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
