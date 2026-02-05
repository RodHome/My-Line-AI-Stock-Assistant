import os, requests, random, re
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)
user_sessions = {} # 用於記憶 user 最後查詢的標的

# --- 核心：AI 智囊團 (支援 6 金鑰輪替與雙模型分工) ---
def call_gemini(prompt, mode="pro"):
    from google import genai
    # 支援 1 到 6 組金鑰分散流量
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys: keys = [os.environ.get('GEMINI_API_KEY')]
    
    # 分析用 Pro (具備 Thinking 推理)，輔助用 Flash (極速節流)
    model_name = "models/gemini-2.5-pro" if mode == "pro" else "models/gemini-2.5-flash"
    selected_key = random.choice(keys)
    client = genai.Client(api_key=selected_key)
    
    try:
        res = client.models.generate_content(model=model_name, contents=prompt)
        return res.text
    except Exception as e:
        # 自動降級：Pro 429 報錯時自動轉 Flash 補位
        if mode == "pro":
            try:
                res = client.models.generate_content(model="models/gemini-2.5-flash", contents=prompt)
                return "⚠️(Pro 滿載，已切換 Flash 專業分析)\n\n" + res.text
            except: pass
        return f"☢️ 系統忙碌，請稍後重試。(K{keys.index(selected_key)+1})"

# --- 核心：數據引擎 (使用 Yahoo 獲取 OHLCV 資料) ---
def fetch_stock_data(sid):
    headers = {'User-Agent': 'Mozilla/5.0'}
    for ext in [".TW", ".TWO"]: # 支援上市與上櫃
        try:
            # 抓取 1mo (一個月) 資料確保擁有 20-22 個交易日
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sid}{ext}?range=1mo&interval=1d"
            res = requests.get(url, headers=headers, timeout=5).json()
            result = res['chart']['result'][0]
            # 抓取收盤價 p 與成交量 v
            p = [x for x in result['indicators']['quote'][0]['close'] if x]
            v = [x for x in result['indicators']['quote'][0]['volume'] if x]
            if len(p) >= 20: 
                return {"id": sid, "p": p[-22:], "v": v[-22:], "m": "上市" if ext==".TW" else "上櫃"}
        except: continue
    return None

def get_smart_analysis(query, user_id):
    # 1. 識別代碼 (Flash 處理)
    nums = re.findall(r'\d{4,6}', query)
    is_recommend = "推薦" in query
    
    # 2. 如果是推薦模式：由 AI 挑選 3 支符合條件的標的
    if is_recommend:
        prompt = f"請根據指令『{query}』挑選 3 支台股標的(需符合當沖/深蹲/高息條件)，僅回傳代碼格式如: 代碼, 代碼, 代碼。"
        nums = re.findall(r'\d{4,6}', call_gemini(prompt, mode="flash"))
    
    # 3. 處理名稱轉代碼 (若 user 只打名稱)
    if not nums and re.search(r'[\u4e00-\u9fff]', query):
        nums = re.findall(r'\d{4,6}', call_gemini(f"將『{query}』轉代碼，只回數字。", mode="flash"))

    if not nums: return None, ""

    # 4. 抓取數據：動態數量判斷 (避免湊人數)
    limit = 3 if is_recommend else len(nums)
    data_list, found_ids = [], []
    for sid in nums[:limit]:
        d = fetch_stock_data(sid)
        if d:
            data_list.append(d); found_ids.append(sid)
    
    if not data_list: return "❌ 市場暫無數據或代碼錯誤。", ""

    # 5. 智慧任務切換 (高股息/入手價/一般分析)
    type_mode = "normal"
    if any(k in query for k in ["入", "買", "價", "點"]): type_mode = "price"
    if any(k in query for k in ["高股息", "配息", "殖利率"]): type_mode = "dividend"

    task_map = {
        "price": "【深度入手策略：含具體買點、分批位階與防守點】",
        "dividend": "【高股息伏擊分析：預估股息、殖利率與公告前佈局策略】",
        "normal": "【專業診斷：含 MA20 趨勢、量價結構與產業前景】"
    }
    
    # 強制 AI 使用內部知識庫進行深度推理
    extra_instr = "即便數據有限，請務必結合你對該公司 2024-2025 的獲利認知給予『卓見』，不要只描述數據。"
    prompt = f"今日：2026-02-05。數據：{data_list}。任務：{task_map[type_mode]}。{extra_instr}。繁體中文。"
    
    return call_gemini(prompt, mode="pro"), " ".join(found_ids)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))
    line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))

    @handler.add(MessageEvent, message=TextMessage)
    def handle_message(event):
        uid = event.source.user_id
        msg = event.message.text.strip()
        
        # 智慧導引小抄
        help_msg = "📊 **AI 助理指令小抄**\n\n1. 『推薦 散熱 當沖標的』\n2. 『推薦 半導體 底部起漲股』\n3. 『推薦 營建 高股息潛力股』\n4. 『分析 6683』\n\n💡 完畢後，問『那入手價呢？』"

        if msg in ["幫助", "help", "不會用"]:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=help_msg))
            return

        try:
            # 處理追問：如果 user 沒打代碼，自動補上最後查詢的代碼
            query_content = msg
            if any(k in msg for k in ["入", "買", "價", "點"]) and uid in user_sessions:
                query_content = f"{user_sessions[uid]} {msg}"
            
            ans, last_ids = get_smart_analysis(query_content, uid)
            if last_ids: user_sessions[uid] = last_ids
            
            reply = ans if ans else help_msg
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        except:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 系統校準中，請稍後重試。"))

    try: handler.handle(body, signature)
    except: abort(400)
    return 'OK'
