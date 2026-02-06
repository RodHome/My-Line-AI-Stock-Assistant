import os, requests, random, time
from datetime import datetime, timedelta
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from google import genai

app = Flask(__name__)

# --- 1. 超級快取 (台積電直接查，不問 AI) ---
STOCK_CACHE = {
    "台積電": "2330", "tsmc": "2330",
    "鴻海": "2317", "聯發科": "2454",
    "長榮": "2603", "陽明": "2609", "萬海": "2615",
    "廣達": "2382", "緯創": "3231", "技嘉": "2376"
}

# --- 2. 初始化 LINE 與環境變數 ---
line_bot_api = LineBotApi(os.environ.get('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('LINE_CHANNEL_SECRET'))

def call_gemini_v2(prompt):
    """
    V2 改版重點：
    1. 改用 gemini-1.5-flash (最穩定)
    2. 增加錯誤日誌印出
    """
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys and os.environ.get('GEMINI_API_KEY'):
        keys = [os.environ.get('GEMINI_API_KEY')]
    
    random.shuffle(keys)
    
    for key in keys:
        try:
            client = genai.Client(api_key=key)
            # 隨機冷卻 1~2 秒
            time.sleep(random.uniform(1.0, 2.0))
            # 💡 改用 1.5-flash，避開 2.0 的限制
            res = client.models.generate_content(model="gemini-1.5-flash", contents=prompt)
            if res.text: return res.text, "Active"
        except Exception as e:
            print(f"⚠️ Key Error: {e}") # 這行會顯示在 Runtime Logs
            continue
    return None, "Limit"

def get_stock_id(u_input):
    # 1. 先查快取 (100% 成功)
    if u_input in STOCK_CACHE: return STOCK_CACHE[u_input]
    if u_input.isdigit() and len(u_input) >= 4: return u_input
    
    # 2. 查不到才問 AI
    print(f">>> 觸發 AI 辨識: {u_input}")
    res, _ = call_gemini_v2(f"Identify Taiwan stock ID for '{u_input}'. Reply ONLY 4-digit ID.")
    
    if res and res.strip().isdigit():
        code = res.strip()
        STOCK_CACHE[u_input] = code
        return code
    return None

def fetch_price(stock_id):
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    # 拉長日期範圍，避免假日沒資料
    start = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
    params = {
        "dataset": "TaiwanStockPrice",
        "data_id": stock_id,
        "start_date": start,
        "token": token
    }
    try:
        res = requests.get(url, params=params, timeout=5)
        data = res.json().get('data', [])
        return data[-1] if data else None
    except Exception as e:
        print(f"❌ 報價 API 錯誤: {e}")
        return None

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
    
    # A. 辨識
    stock_id = get_stock_id(u_text)
    if not stock_id:
        # 💡 這裡改了！如果看到這個回覆代表更新成功
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 抱歉，我不認識「{u_text}」，請輸入代號 (如 2330)。"))
        return

    # B. 報價
    data = fetch_price(stock_id)
    if not data:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 找不到 {stock_id} 的資料。"))
        return

    # C. AI 簡評
    prompt = f"股票 {stock_id} 現價 {data['close']}。請用繁體中文給一句話短評與操作建議。"
    ai_ans, status = call_gemini_v2(prompt)
    comment = ai_ans if ai_ans else "💡 AI 休息中，請參考上方報價。"
    
    # D. 回覆 (包含 v2.0 標記)
    reply = (
        f"📊 {stock_id} 最新報價\n"
        f"💰 價格: {data['close']}\n"
        f"------------------\n"
        f"🤖 AI: {comment}\n"
        f"(系統: {status} | v2.0)"
    )
    
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
