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
    except: return "AI 忙碌中，請稍後。"

# --- 核心：Yahoo 數據 (價格保證) ---
def fetch_yahoo_data(sid):
    # 自動偵測上市(.TW)或上櫃(.TWO)
    for ext in [".TW", ".TWO"]:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sid}{ext}?range=2mo&interval=1d"
            res = requests.get(url, timeout=5).json()['chart']['result'][0]
            prices = [p for p in res['indicators']['quote'][0]['close'] if p]
            if prices: return {"prices": prices[-35:], "source": f"Yahoo{ext}"}
        except: continue
    return None

# --- 輔助：FinMind 數據 (籌碼嘗試) ---
def fetch_chips_safe(sid):
    token = os.environ.get('FINMIND_TOKEN', '')
    if not token: return []
    try:
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInstitutionalInvestorsBuySell&stock_id={sid}&start_date=2026-01-01&token={token}"
        res = requests.get(url, timeout=5).json()
        # 如果 status 不是 200，代表權限不足，回傳空清單
        if res.get('status') != 200: return []
        return res.get('data', [])[-10:]
    except: return []

def get_analysis(query, mode="normal"):
    # 提取代碼 (支援直接打 6683 或 分析 雍智)
    nums = re.findall(r'\d{4,6}', query)
    if not nums:
        prompt = f"將『{query}』轉為台股代碼，只回傳代碼。例：雍智科技回傳6683。"
        num_res = call_gemini(prompt, use_pro=False)
        nums = re.findall(r'\d{4,6}', num_res)
    
    if not nums: return "❌ 無法識別代碼，請輸入如『2330』。", ""

    all_results = []
    for sid in nums:
        p_data = fetch_yahoo_data(sid)
        if p_data:
            p_data['id'] = sid
            p_data['chips'] = fetch_chips_safe(sid)
            all_results.append(p_data)
    
    if not all_results: return "❌ 資料庫連線失敗，請檢查網路。", ""

    task = "入手價與點位建議" if mode == "price" else "全面診斷"
    prompt = f"數據:{all_results}。任務:{task}。格式:1.股票(代號) 分析(含MA20、量價、籌碼)。繁體中文。"
    return call_gemini(prompt), " ".join(nums)

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
            # 追問邏輯
            if any(k in msg for k in ["入手", "買", "多少", "價"]) and uid in user_sessions:
                ans, _ = get_analysis(user_sessions[uid], mode="price")
                reply = f"針對『{user_sessions[uid]}』：\n\n{ans}"
            elif "推薦" in msg:
                user_sessions[uid] = "2303 3481 2409"
                reply = "🚀 潛力股：聯電、群創、友達。可問『入手價是多少？』"
            else:
                ans, last_ids = get_analysis(msg)
                user_sessions[uid] = last_ids
                reply = ans
            
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"⚠️ 服務暫時中斷：{str(e)}"))

    try: handler.handle(body, signature)
    except: abort(400)
    return 'OK'

if __name__ == "__main__":
    app.run()
