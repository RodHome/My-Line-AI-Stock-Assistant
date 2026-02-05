import os, requests, random, re
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = Flask(__name__)
user_sessions = {}

def call_gemini(prompt, model_type="pro"):
    from google import genai
    # 支援 1 到 6 組跨帳號金鑰
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys: keys = [os.environ.get('GEMINI_API_KEY')]
    
    # 修正 404 報錯：使用絕對穩定的模型路徑名稱
    # 分析用 Pro (展現卓見)，輔助用 Flash (快速精簡)
    target_model = "gemini-1.5-pro" if model_type == "pro" else "gemini-1.5-flash"
    
    selected_key = random.choice(keys)
    client = genai.Client(api_key=selected_key)
    
    try:
        res = client.models.generate_content(model=target_model, contents=prompt)
        return res.text
    except Exception as e:
        # 如果 Pro 再次遇到 429 或其他限制，自動強制降級 Flash 確保回覆
        if model_type == "pro":
            try:
                res = client.models.generate_content(model="gemini-1.5-flash", contents=prompt)
                return "⚠️(Pro 配額暫滿，已切換 Flash 專業版分析)\n\n" + res.text
            except: pass
        return f"☢️ 系統異常 (Key {keys.index(selected_key)+1})：{str(e)[:60]}"

def fetch_minimal_data(sid):
    headers = {'User-Agent': 'Mozilla/5.0'}
    # 抓取 1mo 剛好覆蓋 20 個交易日 (MA20 完整呈現)
    for ext in [".TW", ".TWO"]:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sid}{ext}?range=1mo&interval=1d"
            res = requests.get(url, headers=headers, timeout=5).json()
            p = [x for x in res['chart']['result'][0]['indicators']['quote'][0]['close'] if x]
            # 確保數據足以完整分析 20 日均線脈絡
            if len(p) >= 20: return {"id": sid, "p": p[-22:], "m": ext}
        except: continue
    return None

def get_analysis(query, mode="normal"):
    # 名稱轉代碼：Flash 處理，極速節流
    nums = re.findall(r'\d{4,6}', query)
    if not nums and re.search(r'[\u4e00-\u9fff]', query):
        nums = re.findall(r'\d{4,6}', call_gemini(f"將『{query}』轉代碼，只回數字。", model_type="flash"))
    
    if not nums: return "🔍 請提供代碼或名稱 (如: 分析 6683)。", ""

    data_list = []
    for sid in nums:
        d = fetch_minimal_data(sid)
        if d: data_list.append(d)
    
    if not data_list: return "❌ 查無市場數據。", ""

    # 分析與策略：Pro 處理，展現深度見解
    task = "【入手建議與點位】" if mode=="price" else "【MA20 趨勢與量價診斷】"
    prompt = f"數據:{data_list}。任務:{task}。要求:擔任首席分析師，針對個股提供深度見解，文字精煉。繁體中文。"
    
    return call_gemini(prompt, model_type="pro"), " ".join(nums)

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
            # 智慧追問
            if any(k in msg for k in ["入", "買", "價", "點"]) and uid in user_sessions:
                ans, _ = get_analysis(user_sessions[uid], mode="price")
                reply = f"針對『{user_sessions[uid]}』的深度策略：\n\n{ans}"
            elif "推薦" in msg:
                user_sessions[uid] = "2303 3481 2409"
                reply = "🚀 今日推薦：1.聯電 2.群創 3.友達。\n(您可以追問『入手價？』)"
            else:
                ans, last_ids = get_analysis(msg)
                if last_ids: user_sessions[uid] = last_ids
                reply = ans
            
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        except: pass

    try: handler.handle(body, signature)
    except: abort(400)
    return 'OK'
