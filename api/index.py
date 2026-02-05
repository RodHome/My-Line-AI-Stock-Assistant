import os, requests, random, re
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)
# 建立簡單的對話記憶，讓 Bot 記住剛才查過哪支股票
user_sessions = {}

def call_gemini(prompt, use_pro=True):
    from google import genai
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 5) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys: keys = [os.environ.get('GEMINI_API_KEY')]
    client = genai.Client(api_key=random.choice(keys))
    try:
        res = client.models.generate_content(model="gemini-2.5-pro" if use_pro else "gemini-2.5-flash", contents=prompt)
        return res.text
    except Exception as e:
        return f"AI 頻道忙碌，請稍後再試。"

# --- 核心：Yahoo 數據抓取 (自動識別上市櫃、保證有資料) ---
def fetch_yahoo_stable(sid):
    headers = {'User-Agent': 'Mozilla/5.0'}
    # 依序嘗試上市 (.TW) 與 上櫃 (.TWO)
    for ext in [".TW", ".TWO"]:
        try:
            # 抓取 2 個月資料，確保有足夠的 35 筆交易日來算 MA20
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sid}{ext}?range=2mo&interval=1d"
            res = requests.get(url, headers=headers, timeout=5).json()
            result = res['chart']['result'][0]
            prices = [p for p in result['indicators']['quote'][0]['close'] if p]
            vols = [v for v in result['indicators']['quote'][0]['volume'] if v]
            if prices and len(prices) > 20:
                return {"id": sid, "prices": prices[-40:], "vols": vols[-40:], "mkt": ext}
        except: continue
    return None

def get_diagnosis(query, mode="normal"):
    # 1. 提取所有數字代碼
    nums = re.findall(r'\d{4,6}', query)
    # 2. 如果沒數字有中文，問 AI 轉代碼
    if not nums and re.search(r'[\u4e00-\u9fff]', query):
        prompt = f"將『{query}』轉為台股代碼，只回傳純數字(如: 6683)。"
        nums = re.findall(r'\d{4,6}', call_gemini(prompt, use_pro=False))
    
    if not nums: return "🔍 找不到代碼。請輸入如「2330」或「分析 雍智」。", ""

    final_results = []
    found_ids = []
    for sid in nums:
        data = fetch_yahoo_stable(sid)
        if data:
            final_results.append(data)
            found_ids.append(sid)
    
    if not final_results: return f"❌ 代碼 {nums} 在市場中找不到數據。", ""

    # 3. 根據追問模式調整 Prompt
    task = "【入手價建議與分批進場策略】" if mode == "price" else "【趨勢、量價與籌碼推論】"
    prompt = f"數據:{final_results}。任務:{task}。格式:1.股票名稱(代號) 分析結果(包含MA20、量價、點位建議)。繁體中文。"
    
    return call_gemini(prompt), " ".join(found_ids)

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
            # 智慧追問功能：如果訊息包含「入手/買/點位/價格」且有上次查詢紀錄
            price_keywords = ["入手", "買", "多少", "價", "點位", "進場"]
            if any(k in msg for k in price_keywords) and uid in user_sessions:
                ans, _ = get_diagnosis(user_sessions[uid], mode="price")
                reply = f"針對『{user_sessions[uid]}』的入手分析：\n\n{ans}"
            
            elif "推薦" in msg:
                user_sessions[uid] = "2303 3481 2409"
                reply = "🚀 AI 潛力股：聯電(2303)、群創(3481)、友達(2409)。\n\n您可以接著問：『入手價是多少？』"
            
            else:
                ans, last_ids = get_diagnosis(msg)
                if last_ids: user_sessions[uid] = last_ids # 儲存代碼供下次追問
                reply = ans
            
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"⚠️ 系統稍忙，請再試一次。"))

    try: handler.handle(body, signature)
    except: abort(400)
    return 'OK'
