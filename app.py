import os, requests, random, time, re
import json
import concurrent.futures
from datetime import datetime, timedelta
from flask import Flask, request, abort

# 🟢 [升級] 引入 SDK v3 的新模組
from linebot.v3 import (
    WebhookHandler
)
from linebot.v3.exceptions import (
    InvalidSignatureError
)
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
    TextMessageContent
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent as WebhookTextMessageContent
)

app = Flask(__name__)

# 🟢 [版本號] v10.1-Modern (Python 3.13 Compatible)
BOT_VERSION = "v10.1-Modern"

# --- 1. 菁英股票池 (維持不變) ---
STOCK_CACHE = {
    "台積電": "2330", "鴻海": "2317", "聯發科": "2454", "廣達": "2382",
    "緯創": "3231", "技嘉": "2376", "台達電": "2308", "日月光": "3711",
    "聯電": "2303", "瑞昱": "2379", "聯詠": "3034", "華碩": "2357",
    "研華": "2395", "智邦": "2345", "大立光": "3008", "光寶科": "2301",
    "緯穎": "6669", "矽力": "6415", "南亞科": "2408", "友達": "2409",
    "群創": "3481", "微星": "2377", "英業達": "2356", "仁寶": "2324",
    "京元電": "2449", "力積電": "6770", "華邦電": "2344", "佳世達": "2352",
    "聯強": "2347", "大聯大": "3702", "文曄": "3036", "健鼎": "3044",
    "欣興": "3037", "南電": "8046", "景碩": "3189", "台光電": "2383",
    "台燿": "6274", "金像電": "2368", "奇鋐": "3017", "雙鴻": "3324",
    "建準": "2421", "力致": "3483", "愛普": "6531", "智原": "3035",
    "創意": "3443", "世芯": "3661", "M31": "6643", "祥碩": "5269",
    "嘉澤": "3533", "致茂": "2360", "義隆": "2458", "新唐": "4919",
    "威剛": "3260", "群聯": "8299", "十銓": "4967", 
    "增你強": "3028", "強茂": "2481", "超豐": "2441",
    "富邦金": "2881", "國泰金": "2882", "中信金": "2891", "兆豐金": "2886",
    "玉山金": "2884", "元大金": "2885", "第一金": "2892", "合庫金": "5880",
    "華南金": "2880", "台新金": "2887", "永豐金": "2890", "凱基金": "2883",
    "彰銀": "2801", "臺企銀": "2834", "遠東銀": "2845",
    "台泥": "1101", "亞泥": "1102", "台塑": "1301", "南亞": "1303",
    "台化": "1326", "台塑化": "6505", "遠東新": "1402", "中鋼": "2002",
    "豐興": "2015", "大成鋼": "2027", "統一": "1216", "統一超": "2912",
    "和泰車": "2207", "裕隆": "2201", "巨大": "9921",
    "長榮": "2603", "陽明": "2609", "萬海": "2615", "長榮航": "2618",
    "華航": "2610", "慧洋": "2637", "裕民": "2606", "華城": "1519",
    "士電": "1503", "中興電": "1513", "東元": "1504", "亞力": "1514",
    "世紀鋼": "9958", "上緯": "3708", "正隆": "1904", "山隆": "2616", "榮剛": "5009"
}

CODE_TO_NAME = {v: k for k, v in STOCK_CACHE.items()}

DAY_TRADE_BROKERS = """👹 **【知名隔日沖分點名單】**
1. **凱基-台北**
2. **元大-土城永寧**
3. **富邦-建國**
4. **群益-大安**
5. **美好-大安**
6. **國票-敦北法人**
7. **統一-士林**

⚠️ **特徵**：今日大買鎖漲停，明日開高慣殺。"""

# 🟢 [升級] 設定 SDK v3
channel_access_token = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
channel_secret = os.environ.get('LINE_CHANNEL_SECRET')

if not channel_access_token or not channel_secret:
    print('⚠️ 警告：請設定 LINE_CHANNEL_ACCESS_TOKEN 與 LINE_CHANNEL_SECRET')

configuration = Configuration(access_token=channel_access_token)
handler = WebhookHandler(channel_secret)

@app.route("/")
def health_check():
    return f"OK (Bot Version: {BOT_VERSION})", 200

# 🟢 [輔助] 簡化 v3 回覆訊息的函式
def reply_text(reply_token, text):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text)]
            )
        )

# --- AI 與 股票邏輯 (維持不變) ---
def call_gemini_v10_1(prompt, system_instruction=None):
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys and os.environ.get('GEMINI_API_KEY'):
        keys = [os.environ.get('GEMINI_API_KEY')]
    
    if not keys: return None, "NoKeys"
    random.shuffle(keys)
    max_tokens = 2000
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

def fetch_data_light(stock_id):
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        start = (datetime.now() - timedelta(days=150)).strftime('%Y-%m-%d')
        res = requests.get(url, params={"dataset": "TaiwanStockPrice", "data_id": stock_id, "start_date": start, "token": token}, headers=headers, timeout=5)
        data = res.json().get('data', [])
        if not data: return None
        
        latest = data[-1]
        closes = [d['close'] for d in data]
        highs = [d['max'] for d in data]
        
        ma5 = round(sum(closes[-5:]) / 5, 2) if len(closes) >= 5 else 0
        ma20 = round(sum(closes[-20:]) / 20, 2) if len(closes) >= 20 else 0
        ma60 = round(sum(closes[-60:]) / 60, 2) if len(closes) >= 60 else 0
        
        slope_ma20 = 0
        if len(closes) >= 25:
            prev_ma20 = round(sum(closes[-25:-5]) / 20, 2)
            if prev_ma20 > 0:
                slope_ma20 = round((ma20 - prev_ma20) / prev_ma20 * 100, 2)

        high_60 = max(highs[-60:]) if len(highs) >= 60 else max(highs)
        bias_60 = 0
        if ma60 > 0: bias_60 = round((latest['close'] - ma60) / ma60 * 100, 1)

        is_squeeze = False
        if ma5 > 0 and ma20 > 0 and ma60 > 0:
            mas = [ma5, ma20, ma60]
            if (max(mas) - min(mas)) / min(mas) < 0.03: is_squeeze = True

        return {
            "code": stock_id, 
            "close": latest['close'], 
            "ma5": ma5, "ma20": ma20, "ma60": ma60,
            "slope_ma20": slope_ma20,
            "high_60": high_60,
            "bias_60": bias_60,
            "is_squeeze": is_squeeze,
            "volatility": round((latest['max'] - latest['min']) / latest['close'] * 100, 1) if latest['close'] > 0 else 0
        }
    except: return None

def fetch_chips_accumulate(stock_id):
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        start = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d')
        res = requests.get(url, params={"dataset": "TaiwanStockInstitutionalInvestorsBuySell", "data_id": stock_id, "start_date": start, "token": token}, headers=headers, timeout=5)
        data = res.json().get('data', [])
        if not data: return 0, 0, 0, 0
        
        latest_date = data[-1]['date']
        today_f = 0; today_t = 0
        unique_dates = sorted(list(set([d['date'] for d in data])), reverse=True)[:5]
        acc_f = 0; acc_t = 0
        
        for row in data:
            if row['date'] in unique_dates:
                val = row['buy'] - row['sell']
                if row['name'] == 'Foreign_Investor':
                    acc_f += val
                    if row['date'] == latest_date: today_f = val
                elif row['name'] == 'Investment_Trust':
                    acc_t += val
                    if row['date'] == latest_date: today_t = val
        return int(today_f/1000), int(today_t/1000), int(acc_f/1000), int(acc_t/1000)
    except: return 0, 0, 0, 0

def fetch_full_data(stock_id):
    basic = fetch_data_light(stock_id)
    if not basic: return None
    tf, tt, af, at = fetch_chips_accumulate(stock_id)
    basic.update({'foreign': tf, 'trust': tt, 'acc_foreign': af, 'acc_trust': at})
    return basic

def fetch_eps(stock_id):
    if stock_id.startswith("00"): return "ETF"
    token = os.environ.get('FINMIND_TOKEN', '')
    start = (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d')
    try:
        res = requests.get("https://api.finmindtrade.com/api/v4/data", params={"dataset": "TaiwanStockFinancialStatements", "data_id": stock_id, "start_date": start, "token": token}, timeout=5)
        data = res.json().get('data', [])
        if not data: return "N/A"
        eps_data = [d for d in data if d['type'] == 'EPS']
        if not eps_data: return "N/A"
        latest_year = eps_data[-1]['date'][:4]
        vals = [d['value'] for d in eps_data if d['date'].startswith(latest_year)]
        return f"{latest_year}累計{round(sum(vals), 2)}元"
    except: return "逾時"

def get_stock_id(text):
    text = text.strip()
    clean_text = re.sub(r'(成本|cost).*', '', text, flags=re.IGNORECASE).strip()
    
    if clean_text in STOCK_CACHE: return STOCK_CACHE[clean_text]
    if clean_text.isdigit() and len(clean_text) >= 4: return clean_text
    
    if len(clean_text) > 6 or "推薦" in text or "分點" in text: return None
    
    prompt = f"Identify the 4-digit stock code for Taiwan stock '{clean_text}'. Reply ONLY with the 4-digit number. If NOT stock, return nothing."
    res, _ = call_gemini_v10_1(prompt)
    if res and (match := re.search(r'\d{4}', res)):
        code = match.group(0)
        STOCK_CACHE[clean_text] = code
        return code
    return None

def check_stock_worker_turbo(code):
    try:
        data = fetch_data_light(code)
        if not data: return None
        if data['close'] > data['ma5'] and data['ma5'] > data['ma20'] and data['ma20'] > data['ma60']:
            tf, tt, af, at = fetch_chips_accumulate(code)
            if (af + at) > 0:
                name = CODE_TO_NAME.get(code, code)
                return f"{name}({code}): 三線多頭且5日籌碼集中"
    except: return None
    return None

def scan_recommendations_turbo():
    candidates = []
    sample_list = random.sample(list(STOCK_CACHE.values()), 25)
    sample_list = [c for c in sample_list if not c.startswith("00")]
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(check_stock_worker_turbo, sample_list)
    for res in results:
        if res: candidates.append(res)
        if len(candidates) >= 3: break
    return candidates

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except InvalidSignatureError: abort(400)
    return 'OK'

# 🟢 [升級] 使用 v3 的事件處理裝飾器
@handler.add(MessageEvent, message=WebhookTextMessageContent)
def handle_message(event):
    msg = event.message.text.strip()
    reply_token = event.reply_token # v3 取得 token

    if msg in ["分點", "隔日沖", "券商", "主力"]:
        reply_text(reply_token, DAY_TRADE_BROKERS)
        return

    if msg in ["推薦", "選股"]:
        good_stocks = scan_recommendations_turbo()
        if not good_stocks:
            reply = "⚠️ 掃描了 25 檔菁英股，暫無發現「完美多頭且籌碼集中」之標的。"
        else:
            stocks_str = "\n".join(good_stocks)
            prompt = (
                f"你是投資顧問。篩選出強勢股：\n{stocks_str}\n\n"
                f"任務：給股市小白推薦。\n"
                f"指令：1.燈號+結論 2.格式:🔥[股票]\n[理由]\n[支撐]"
            )
            ai_ans, status = call_gemini_v10_1(prompt)
            reply = f"🎯 **AI 菁英推薦**\n------------------\n{ai_ans}\n------------------\n(系統: {status})"
        reply_text(reply_token, reply)
        return

    if msg.lower() == "debug":
        token_chk = os.environ.get('FINMIND_TOKEN', '')
        ai_res, ai_stat = call_gemini_v10_1("Hi")
        reply = f"🛠️ **v10.1 (Modern) 診斷**\nToken: {'✅' if token_chk else '❌'}\nAI: {ai_stat}\nPy: {os.sys.version.split()[0]}"
        reply_text(reply_token, reply)
        return

    user_cost = None
    cost_match = re.search(r'(成本|cost)[:\s]*(\d+\.?\d*)', msg, re.IGNORECASE)
    if cost_match:
        try: user_cost = float(cost_match.group(2))
        except: pass

    stock_id = get_stock_id(msg)
    if not stock_id:
        reply = (
            "🤖 **功能選單**\n\n"
            "1. 🔍 **個股健檢**：\n輸入「2330」、「鴻海」\n\n"
            "2. 🧮 **持股診斷**：\n輸入「鴻海成本200」\n幫你算停利停損點\n\n"
            "3. 🎯 **潛力推薦**：輸入「推薦」"
        )
        reply_text(reply_token, reply)
        return

    name = CODE_TO_NAME.get(stock_id, stock_id)
    data = fetch_full_data(stock_id)
    if not data:
        reply_text(reply_token, f"❌ 無法讀取 {stock_id} 數據")
        return
    eps = fetch_eps(stock_id)

    signals = []
    if data['slope_ma20'] > 0.5: signals.append("📈 **月線翻揚** (趨勢向上)")
    elif data['slope_ma20'] < -0.3: signals.append("📉 **月線下彎** (趨勢轉弱)")
    
    if data['is_squeeze']: signals.append("⚠️ **均線糾結** (變盤前兆)")
    if data['close'] > data['ma5'] > data['ma20'] > data['ma60']: signals.append("🟢 **三線多頭** (強勢)")
    if data['bias_60'] > 20: signals.append("🔥 **乖離過大** (防回檔)")
    
    acc_f = data['acc_foreign']; acc_t = data['acc_trust']
    if acc_f > 100 and acc_t > 30: signals.append("💰 **雙資囤貨** (5日連買)")
    elif acc_f + acc_t > 100: signals.append("💰 **籌碼集中** (波段偏多)")
    elif acc_f + acc_t < -100: signals.append("💸 **法人提款** (波段偏空)")
    signal_str = "\n".join(signals) if signals else "🟡 **盤整觀望** (無明顯趨勢)"

    sys_prompt = "你是一位白話文投資顧問。直接給結論，不唸數據。字數150字內。"
    if user_cost:
        profit_pct = round((data['close'] - user_cost) / user_cost * 100, 1)
        profit_status = "獲利" if profit_pct > 0 else "虧損"
        user_prompt = (
            f"標的：{stock_id} {name}\n"
            f"現價：{data['close']} (成本：{user_cost}，{profit_status} {profit_pct}%)\n"
            f"技術：MA20={data['ma20']} (斜率{data['slope_ma20']}%)，60日高點={data['high_60']}\n"
            f"籌碼(5日)：外資{data['acc_foreign']}, 投信{data['acc_trust']}\n\n"
            f"任務：給持有者操作建議。\n"
            f"格式：\n"
            f"【診斷】 (給燈號 🟢續抱/🟡減碼/🔴停損，並簡述原因)\n"
            f"【策略】 (根據技術面，明確給出「停利點」與「防守點」的價位數字)"
        )
        footer_msg = ""
    else:
        user_prompt = (
            f"標的：{stock_id} {name}\n"
            f"數據：現價{data['close']} (MA20={data['ma20']}, 60日高={data['high_60']})\n"
            f"籌碼(5日)：外資{data['acc_foreign']}, 投信{data['acc_trust']}\n"
            f"EPS：{eps}\n\n"
            f"任務：給小白操作建議。\n"
            f"格式：\n"
            f"【AI總結】 (🔴賣出/🟡觀望/🟢買進)\n"
            f"【分析】 (趨勢與籌碼解讀)\n"
            f"【建議】 (支撐與壓力價位)"
        )
        footer_msg = f"\n💡 **持有這檔嗎？**\n請輸入『{name}成本xxx』(如：{name}成本{int(data['close']*0.9)})\nAI 幫你算停利停損點！"

    ai_ans, status = call_gemini_v10_1(user_prompt, system_instruction=sys_prompt)
    
    reply = (
        f"📊 **{name}({stock_id})**\n"
        f"💰 現價：{data['close']}\n"
        f"週: {data['ma5']} | 月: {data['ma20']} | 季: {data['ma60']}\n"
        f"外資: {data['foreign']} (5日: {data['acc_foreign']})\n"
        f"投信: {data['trust']} (5日: {data['acc_trust']})\n"
        f"💎 {eps}\n"
        f"------------------\n"
        f"🚩 **訊號快篩**：\n{signal_str}\n"
        f"------------------\n"
        f"{ai_ans}\n"
        f"------------------\n"
        f"(系統: {status} | v10.1-New){footer_msg}"
    )

    reply_text(reply_token, reply)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)