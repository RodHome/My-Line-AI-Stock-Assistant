import os, requests, random, time
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from google import genai

app = Flask(__name__)

# --- 1. 建立記憶快取 (重啟後會重置) ---
# 預先載入熱門股，節省流量
STOCK_CACHE = {
    "台積電": "2330", "鴻海": "2317", "聯發科": "2454", 
    "長榮": "2603", "陽明": "2609", "萬海": "2615",
    "廣達": "2382", "緯創": "3231"
}

# --- 2. 初始化 LINE 與環境變數 ---
line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

def call_gemini_safe(prompt):
    """
    抗壓型 AI 呼叫函式：
    1. 支援 6 支金鑰輪替
    2. 加入隨機延遲 (Jitter) 避開偵測
    """
    # 抓取所有可用的金鑰
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    # 如果沒設編號金鑰，就抓預設的那支
    if not keys and os.environ.get('GEMINI_API_KEY'):
        keys = [os.environ.get('GEMINI_API_KEY')]
    
    random.shuffle(keys) # 隨機洗牌
    
    for key in keys:
        try:
            client = genai.Client(api_key=key)
            # 關鍵優化：暫停 1.5 到 3 秒，模擬人類思考時間
            time.sleep(random.uniform(1.5, 3.0)) 
            res = client.models.generate_content(model="models/gemini-2.0-flash", contents=prompt)
            if res.text: return res.text, "Active"
        except Exception as e:
            print(f"⚠️ Key 失敗: {e}")
            continue
    return None, "Limit"

def get_stock_id(u_input):
    """優先查快取，查不到才問 AI"""
    if u_input.isdigit() and len(u_input) >= 4: return u_input
    if u_input in STOCK_CACHE: return STOCK_CACHE[u_input]
    
    # 簡化 Prompt 以加快速度
    prompt = f"Identify Taiwan stock ID for '{u_input}'. Reply ONLY 4-digit ID."
    res, _ = call_gemini_safe(prompt)
    
    if res and res.strip().isdigit():
        code = res.strip()
        STOCK_CACHE[u_input] = code # 記住它！
        return code
    return None

def fetch_price(stock_id):
    """抓取 FinMind 最新收盤價"""
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    start = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d') # 拉長到5天避免連假無資料
    params = {
        "dataset": "TaiwanStockPrice",
        "data_id": stock_id,
        "start_date": start,
        "token": token
    }
    try:
        data = requests.get(url, params=params, timeout=5).json().get('data', [])
        return data[-1] if data else None
    except: return None

# --- 3. LINE Webhook 入口 ---
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
    print(f">>> 用戶查詢: {u_text}")

    # A. 辨識代碼
    stock_id = get_stock_id(u_text)
    if not stock_id:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 抱歉，我不認識「{u_text}」。請嘗試輸入完整名稱或代號。"))
        return

    # B. 取得報價
    data = fetch_price(stock_id)
    if not data:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 找不到 {stock_id} 的近期報價資料。"))
        return

    # C. AI 分析 (若失敗則給基本回應)
    prompt = f"股票 {stock_id} 現價 {data['close']}。請給一句話短評(20字內)與操作建議。"
    ai_ans, status = call_gemini_safe(prompt)
    
    comment = ai_ans if ai_ans else "💡 AI 休息中，請參考上方報價自行判斷。"
    
    # D. 組合回應
    reply = (
        f"📊 {stock_id} 最新報價\n"
        f"💰 價格: {data['close']}\n"
        f"------------------\n"
        f"🤖 AI: {comment}\n"
        f"(系統狀態: {status})"
    )
    
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    # 配合 Zeabur 規範，讀取環境變數 PORT，預設 8080
    port = int(os.environ.get("PORT", 8080))
    print(f"🚀 啟動成功！正在監聽 Port {port}")
    app.run(host='0.0.0.0', port=port)
