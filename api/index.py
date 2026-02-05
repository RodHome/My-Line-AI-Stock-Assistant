import os, requests, random, re
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)
user_sessions = {}

def call_gemini(prompt, use_pro=True):
    from google import genai
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 5) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys: keys = [os.environ.get('GEMINI_API_KEY')]
    client = genai.Client(api_key=random.choice(keys))
    try:
        res = client.models.generate_content(model="gemini-2.5-pro" if use_pro else "gemini-2.5-flash", contents=prompt)
        return res.text
    except: return "⚠️ AI 頻道滿載，請稍後再試。"

# --- 核心：Yahoo 數據抓取 (保證穩定、不需 Token) ---
def fetch_yahoo_data_stable(sid):
    headers = {'User-Agent': 'Mozilla/5.0'}
    # 自動嘗試上市與上櫃後綴
    for ext in [".TW", ".TWO"]:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sid}{ext}?range=2mo&interval=1d"
            res = requests.get(url, headers=headers, timeout=5).json()
            result = res['chart']['result'][0]
            prices = [p for p in result['indicators']['quote'][0]['close'] if p]
            volumes = [v for v in result['indicators']['quote'][0]['volume'] if v]
            if prices: return {"prices": prices[-35:], "volumes": volumes[-35:], "market": ext}
        except: continue
    return None

def get_analysis_final(query, mode="normal"):
    # 提取代碼 (支援: 2330, 分析 雍智, 6683)
    nums = re.findall(r'\d{4,6}', query)
    if not nums:
        prompt = f"請將文字『{query}』轉換為台灣股票代碼。只需回傳代碼，例如：雍智科技回傳6683。若非股票請回傳空。"
        nums = re.findall(r'\d{4,6}', call_gemini(prompt, use_pro=False))
    
    if not nums: return "❌ 識別不到股票代碼，請輸入如「2330」或「分析 雍智」。", ""

    stock_results = []
    valid_ids = []
    for sid in nums:
        data = fetch_yahoo_data_stable(sid)
        if data:
            data['id'] = sid
            stock_results.append(data)
            valid_ids.append(sid)
    
    if not stock_results: return f"❌ 代碼 {nums} 在市場中找不到數據。", ""

    # 根據模式調整分析重心
    focus = "【具體入手價、支撐位與進場策略】" if mode == "price" else "【趨勢、量價與籌碼推論】"
    prompt = f"數據：{stock_results}。任務：{focus}。要求：1.名稱(代號) 分析診斷。請用繁體中文，專業且精煉。"
    
    return call_gemini(prompt), " ".join(valid_ids)

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
            # 1. 追問入手價
            if any(k in msg for k in ["入手", "買", "多少", "價", "進場"]) and uid in user_sessions:
                ans, _ = get_analysis_final(user_sessions[uid], mode="price")
                reply = f"針對『{user_sessions[uid]}』的入手分析：\n\n{ans}"
            
            # 2. 推薦邏輯
            elif "推薦" in msg:
                user_sessions[uid] = "2303 3481 2409"
                reply = "🚀 AI 推薦深蹲股：聯電、群創、友達。\n\n您可以接著問：『入手價是多少？』"
            
            # 3. 一般分析
            else:
                ans, last_ids = get_analysis_final(msg)
                if last_ids: user_sessions[uid] = last_ids
                reply = ans
            
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"⚠️ 系統繁忙，請再試一次。"))

    try: handler.handle(body, signature)
    except: abort(400)
    return 'OK'
