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
    
    # 選用模型：分析用 Pro，輔助用 Flash
    target_model = "gemini-2.0-pro" if model_type == "pro" else "gemini-2.0-flash"
    
    selected_key = random.choice(keys)
    client = genai.Client(api_key=selected_key)
    
    try:
        res = client.models.generate_content(model=target_model, contents=prompt)
        return res.text
    except Exception as e:
        # 如果 Pro 爆流量 (429)，自動降級為 Flash 補位
        if "429" in str(e) and model_type == "pro":
            try:
                res = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
                return "⚠️(Pro 配額滿載，已切換 Flash 診斷)\n\n" + res.text
            except: pass
        return f"☢️ 系統異常：{str(e)[:50]}"

def fetch_minimal_data(sid):
    headers = {'User-Agent': 'Mozilla/5.0'}
    # 抓取 1mo 確保擁有計算 MA20 的最低數據量 (約 22 筆)
    for ext in [".TW", ".TWO"]:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sid}{ext}?range=1mo&interval=1d"
            res = requests.get(url, headers=headers, timeout=5).json()
            p = [x for x in res['chart']['result'][0]['indicators']['quote'][0]['close'] if x]
            # 確保數據足以分析 20 日均線
            if len(p) >= 20: return {"id": sid, "p": p[-22:], "m": ext}
        except: continue
    return None

def get_analysis(query, mode="normal"):
    # 股票名稱轉代碼：使用 Flash 節省配額
    nums = re.findall(r'\d{4,6}', query)
    if not nums and re.search(r'[\u4e00-\u9fff]', query):
        nums = re.findall(r'\d{4,6}', call_gemini(f"將『{query}』轉為台股代碼，只回傳數字。", model_type="flash"))
    
    if not nums: return "🔍 請輸入代碼或股票名稱。", ""

    data_list = []
    for sid in nums:
        d = fetch_minimal_data(sid)
        if d: data_list.append(d)
    
    if not data_list: return "❌ 查無市場數據。", ""

    # 深度診斷與入手價：強制使用 Pro 展現見解
    task = "【具體入手價、支撐點位與分批進場策略】" if mode=="price" else "【MA20 趨勢、量價型態與專業深度診斷】"
    prompt = f"數據:{data_list}。任務:{task}。請扮演首席分析師給予精確見解。繁體中文。"
    
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
            if any(k in msg for k in ["入", "買", "價", "點"]) and uid in user_sessions:
                # 追問模式
                ans, _ = get_analysis(user_sessions[uid], mode="price")
                reply = f"針對『{user_sessions[uid]}』的深度分析：\n\n{ans}"
            elif "推薦" in msg:
                # 推薦模式：使用 Flash 即可
                user_sessions[uid] = "2303 3481 2409"
                reply = "🚀 AI 嚴選推薦：1.聯電 2.群創 3.友達。\n\n您可以接著問：『入手價建議？』"
            else:
                # 一般分析模式：Pro 優先
                ans, last_ids = get_analysis(msg)
                if last_ids: user_sessions[uid] = last_ids
                reply = ans
            
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        except: pass

    try: handler.handle(body, signature)
    except: abort(400)
    return 'OK'
