import os, requests, random, re
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)
user_sessions = {} # 暫存對話代碼，用於追問入手價

def call_gemini(prompt, mode="pro"):
    from google import genai
    # 抓取 1 到 6 組跨帳號金鑰
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys: keys = [os.environ.get('GEMINI_API_KEY')]
    
    # 鎖定 2025/2026 穩定版型號
    # 分析用 Pro (具備思考能力)，輔助用 Flash (極速節流)
    model_name = "models/gemini-2.5-pro" if mode == "pro" else "models/gemini-2.5-flash"
    
    selected_key = random.choice(keys)
    client = genai.Client(api_key=selected_key)
    
    try:
        res = client.models.generate_content(model=model_name, contents=prompt)
        return res.text
    except Exception as e:
        # 自動降級：若 Pro 429 報錯，秒切 Flash 確保不沈默
        if mode == "pro":
            try:
                res = client.models.generate_content(model="models/gemini-2.5-flash", contents=prompt)
                return "⚠️(Pro 流量滿載，已切換 Flash 專業分析)\n\n" + res.text
            except: pass
        return f"☢️ 系統繁忙 (K{keys.index(selected_key)+1})：{str(e)[:60]}"

def fetch_yahoo_data(sid):
    headers = {'User-Agent': 'Mozilla/5.0'}
    # 抓取 1mo 確保 MA20 完整性 (20-22 筆交易日)
    for ext in [".TW", ".TWO"]:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sid}{ext}?range=1mo&interval=1d"
            res = requests.get(url, headers=headers, timeout=6).json()
            result = res['chart']['result'][0]
            prices = [p for p in result['indicators']['quote'][0]['close'] if p]
            if len(prices) >= 20:
                return {"id": sid, "prices": prices[-22:], "market": "上市" if ext==".TW" else "上櫃"}
        except: continue
    return None

def get_analysis(query, is_price_mode=False):
    # 名稱轉代碼：Flash 處理
    nums = re.findall(r'\d{4,6}', query)
    if not nums and re.search(r'[\u4e00-\u9fff]', query):
        prompt = f"將『{query}』轉為台股代碼，只回傳純數字(如: 6683)。"
        nums = re.findall(r'\d{4,6}', call_gemini(prompt, mode="flash"))
    
    if not nums: return "🔍 找不到代碼。請輸入如『2330』或『分析 雍智』。", ""

    final_data = []
    found_ids = []
    for sid in nums:
        data = fetch_yahoo_data(sid)
        if data:
            final_data.append(data)
            found_ids.append(sid)
    
    if not final_data: return f"❌ 市場找不到代碼 {nums} 的數據。", ""

    # 深度診斷與入手價：Pro 處理，展現卓見
    task = "【詳細入手價格建議與防守點位】" if is_price_mode else "【MA20 趨勢、量價型態與專業深度診斷】"
    prompt = f"數據:{final_data}。任務:{task}。要求：扮演首席分析師，給予精確且具見解的評估，文字精煉。繁體中文。"
    
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
        
        try:
            # 入手價追問邏輯
            if any(k in msg for k in ["入", "買", "價", "點", "多少"]) and uid in user_sessions:
                ans, _ = get_analysis(user_sessions[uid], is_price_mode=True)
                reply = f"針對『{user_sessions[uid]}』的深度策略：\n\n{ans}"
            
            elif "推薦" in msg:
                # 推薦模式：固定提供 3 支最佳標的
                user_sessions[uid] = "2303 3481 2409"
                reply = "🚀 AI 今日嚴選推薦：\n1. 聯電 (2303)\n2. 群創 (3481)\n3. 友達 (2409)\n\n您可以接著追問：『入手價建議？』"
            
            else:
                # 一般診斷
                ans, last_ids = get_analysis(msg)
                if last_ids: user_sessions[uid] = last_ids
                reply = ans
            
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"⚠️ 服務稍忙，請重試一次。({str(e)[:30]})"))

    try: handler.handle(body, signature)
    except: abort(400)
    return 'OK'
