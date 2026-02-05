import os, requests, random, re, time
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)
user_sessions = {} # 記憶對話代碼

# --- 核心：AI 大腦 (6金鑰輪替 + 雙模型分工) ---
def call_gemini(prompt, mode="pro"):
    from google import genai
    # 支援 1 到 6 組跨帳號金鑰
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys: keys = [os.environ.get('GEMINI_API_KEY')]
    
    # 分析用 Pro (具備 Thinking 能力)，輔助用 Flash (節流)
    model_name = "models/gemini-2.5-pro" if mode == "pro" else "models/gemini-2.5-flash"
    
    selected_key = random.choice(keys)
    client = genai.Client(api_key=selected_key)
    
    try:
        res = client.models.generate_content(model=model_name, contents=prompt)
        return res.text
    except Exception as e:
        # 自動降級機制
        if mode == "pro" and "429" in str(e):
            try:
                res = client.models.generate_content(model="models/gemini-2.5-flash", contents=prompt)
                return "⚠️ (Pro 配額滿載，已切換 Flash 提供深度見解)\n\n" + res.text
            except: pass
        return f"☢️ 系統忙碌 (K{keys.index(selected_key)+1})，請稍後重試。"

# --- 核心：數據中心 (1mo 確保 MA20 最低數據量) ---
def fetch_stock_data(sid):
    headers = {'User-Agent': 'Mozilla/5.0'}
    for ext in [".TW", ".TWO"]: # 支援上市與上櫃
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sid}{ext}?range=1mo&interval=1d"
            res = requests.get(url, headers=headers, timeout=5).json()
            p = [x for x in res['chart']['result'][0]['indicators']['quote'][0]['close'] if x]
            # 確保足以呈現完整的 $MA_{20}$ 指標
            if len(p) >= 20: return {"id": sid, "p": p[-22:], "m": "上市" if ext==".TW" else "上櫃"}
        except: continue
    return None

def get_smart_analysis(query, type_mode="normal"):
    # 1. 識別代碼 (Flash 處理)
    nums = re.findall(r'\d{4,6}', query)
    if not nums and re.search(r'[\u4e00-\u9fff]', query) and len(query) < 10:
        nums = re.findall(r'\d{4,6}', call_gemini(f"將『{query}』轉為台股代碼，只回數字。", mode="flash"))
    
    # 2. 如果是推薦模式 (動態三軌)
    if "推薦" in query or not nums:
        # 這裡由 AI 根據產業別與模式，從知識庫挑選 3 支最優標的
        prompt = f"請根據指令『{query}』，從台灣股市挑選 3 支符合條件(當沖、深蹲波段或高息伏擊)的標的，並註明推薦理由。請只回傳代碼，格式: 代碼, 代碼, 代碼。"
        nums = re.findall(r'\d{4,6}', call_gemini(prompt, mode="flash"))
    
    if not nums: return None, ""

    # 3. 抓取精簡數據 (22 交易日)
    data_list = []
    found_ids = []
    for sid in nums[:3]: # 強制精簡為 3 支
        d = fetch_stock_data(sid)
        if d:
            data_list.append(d)
            found_ids.append(sid)
    
    if not data_list: return "❌ 市場暫無數據，請稍後再試。", ""

    # 4. 深度分析 (Pro 提供卓見)
    task_map = {
        "price": "【深度入手價、分批策略與防守位】",
        "normal": "【MA20 趨勢、量價結構與專業診斷】"
    }
    prompt = f"數據:{data_list}。任務:{task_map.get(type_mode, task_map['normal'])}。要求:擔任首席分析師，針對個股給予具備產業視野的深度見解。繁體中文。"
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
        
        # 智慧引導訊息
        help_msg = (
            "📊 **AI 投資助理使用指南**\n\n"
            "想要精準分析？試著這樣問：\n"
            "1. **當沖**：『推薦 半導體 當沖標的』\n"
            "2. **深蹲**：『推薦 散熱 底部起漲股』\n"
            "3. **高息**：『推薦 營建 高股息潛力股』\n"
            "4. **診斷**：直接打『分析 6683』或『雍智』\n\n"
            "💡 分析完後，問『入手價？』可解鎖深度策略。"
        )

        if msg in ["幫助", "help", "不會用", "你好"]:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=help_msg))
            return

        try:
            # 入手價追問
            if any(k in msg for k in ["入", "買", "價", "多少"]) and uid in user_sessions:
                ans, _ = get_smart_analysis(user_sessions[uid], type_mode="price")
            else:
                ans, last_ids = get_smart_analysis(msg)
                if last_ids: user_sessions[uid] = last_ids
            
            reply = ans if ans else help_msg
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        except:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 系統微調中，請稍後片刻。"))

    try: handler.handle(body, signature)
    except: abort(400)
    return 'OK'
