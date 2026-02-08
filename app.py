import os, requests, random, time, re
import json
import concurrent.futures
from datetime import datetime, timedelta
from flask import Flask, request, abort

# 🟢 [升級] 引入 SDK v3 的新模組
from linebot.v3 import (
    WebhookHandler
)
from linebot.v3.exceptions import (
    InvalidSignatureError
)
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
    TextMessageContent
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent as WebhookTextMessageContent
)

app = Flask(__name__)

# 🟢 [版本號] v10.1-Modern (Python 3.13 Compatible)
BOT_VERSION = "v10.1-Modern"

# --- 1. 菁英股票池 (維持不變) ---
STOCK_CACHE = {
    "台積電": "2330", "鴻海": "2317", "聯發科": "2454", "廣達": "2382",
    "緯創": "3231", "技嘉": "2376", "台達電": "2308", "日月光": "3711",
    "聯電": "2303", "瑞昱": "2379", "聯詠": "3034", "華碩": "2357",
    "研華": "2395", "智邦": "2345", "大立光": "3008", "光寶科": "2301",
    "緯穎": "6669", "矽力": "6415", "南亞科": "2408", "友達": "2409",
    "群創": "3481", "微星": "2377", "英業達": "2356", "仁寶": "2324",
    "京元電": "2449", "力積電": "6770", "華邦電": "2344", "佳世達": "2352",
    "聯強": "2347", "大聯大": "3702", "文曄": "3036", "健鼎": "3044",
    "欣興": "3037", "南電": "8046", "景碩": "3189", "台光電": "2383",
    "台燿": "6274", "金像電": "2368", "奇鋐": "3017", "雙鴻": "3324",
    "建準": "2421", "力致": "3483", "愛普": "6531", "智原": "3035",
    "創意": "3443", "世芯": "3661", "M31": "6643", "祥碩": "5269",
    "嘉澤": "3533", "致茂": "2360", "義隆": "2458", "新唐": "4919",
    "威剛": "3260", "群聯": "8299", "十銓": "4967", 
    "增你強": "3028", "強茂": "2481", "超豐": "2441",
    "富邦金": "2881", "國泰金": "2882", "中信金": "2891", "兆豐金": "2886",
    "玉山金": "2884", "元大金": "2885", "第一金": "2892", "合庫金": "5880",
    "華南金": "2880", "台新金": "2887", "永豐金": "2890", "凱基金": "2883",
    "彰銀": "2801", "臺企銀": "2834", "遠東銀": "2845",
    "台泥": "1101", "亞泥": "1102", "台塑": "1301", "南亞": "1303",
    "台化": "1326", "台塑化": "6505", "遠東新": "1402", "中鋼": "2002",
    "豐興": "2015", "大成鋼": "2027", "統一": "1216", "統一超": "2912",
    "和泰車": "2207", "裕隆": "2201", "巨大": "9921",
    "長榮": "2603", "陽明": "2609", "萬海": "2615", "長榮航": "2618",
    "華航": "2610", "慧洋": "2637", "裕民": "2606", "華城": "1519",
    "士電": "1503", "中興電": "1513", "東元": "1504", "亞力": "1514",
    "世紀鋼": "9958", "上緯": "3708", "正隆": "1904", "山隆": "2616", "榮剛": "5009"
}

CODE_TO_NAME = {v: k for k, v in STOCK_CACHE.items()}

DAY_TRADE_BROKERS = """👹 **【知名隔日沖分點名單】**
1. **凱基-台北**
2. **元大-土城永寧**
3. **富邦-建國**
4. **群益-大安**
5. **美好-大安**
6. **國票-敦北法人**
7. **統一-士林**

⚠️ **特徵**：今日大買鎖漲停，明日開高慣殺。"""

# 🟢 [升級] 設定 SDK v3
channel_access_token = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
channel_secret = os.environ.get('LINE_CHANNEL_SECRET')

if not channel_access_token or not channel_secret:
    print('⚠️ 警告：請設定 LINE_CHANNEL_ACCESS_TOKEN 與 LINE_CHANNEL_SECRET')

configuration = Configuration(access_token=channel_access_token)
handler = WebhookHandler(channel_secret)

@app.route("/")
def health_check():
    return f"OK (Bot Version: {BOT_VERSION})", 200

# 🟢 [輔助] 簡化 v3 回覆訊息的函式
def reply_text(reply_token, text):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text)]
            )
        )

# --- AI 與 股票邏輯 (維持不變) ---
def call_gemini_v10_1(prompt, system_instruction=None):
    keys = [os.environ.get(f'GEMINI_API_KEY_{i}') for i in range(1, 7) if os.environ.get(f'GEMINI_API_KEY_{i}')]
    if not keys and os.environ.get('GEMINI_API_KEY'):
        keys = [os.environ.get('GEMINI_API_KEY')]
    
    if not keys: return None, "NoKeys"
    random.shuffle(keys)
    max_tokens = 2000
    target_models = ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-flash-latest"]

    for model in target_models:
        for key in keys:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                headers = {'Content-Type': 'application/json'}
                params = {'key': key}
                contents = [{"parts": [{"text": prompt}]}]
                if system_instruction:
                    full_prompt = f"【系統指令】：{system_instruction}\n\n【用戶請求】：{prompt}"
                    contents = [{"parts": [{"text": full_prompt}]}]

                payload = {
                    "contents": contents,
                    "generationConfig": {
                        "maxOutputTokens": max_tokens, 
                        "temperature": 0.3
                    }
                }
                response = requests.post(url, headers=headers, params=params, json=payload, timeout=25)
                if response.status_code == 200:
                    data = response.json()
                    text = data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                    if text: return text.strip(), "Active"
                continue
            except: continue
    return "AI 忙碌中", "Timeout"

def fetch_data_light(stock_id):
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        start = (datetime.now() - timedelta(days=150)).strftime('%Y-%m-%d')
        res = requests.get(url, params={"dataset": "TaiwanStockPrice", "data_id": stock_id, "start_date": start, "token": token}, headers=headers, timeout=5)
        data = res.json().get('data', [])
        if not data: return None
        
        latest = data[-1]
        closes = [d['close'] for d in data]
        highs = [d['max'] for d in data]
        
        ma5 = round(sum(closes[-5:]) / 5, 2) if len(closes) >= 5 else 0
        ma20 = round(sum(closes[-20:]) / 20, 2) if len(closes) >= 20 else 0
        ma60 = round(sum(closes[-60:]) / 60, 2) if len(closes) >= 60 else 0
        
        slope_ma20 = 0
        if len(closes) >= 25:
            prev_ma20 = round(sum(closes[-25:-5]) / 20, 2)
            if prev_ma20 > 0:
                slope_ma20 = round((ma20 - prev_ma20) / prev_ma20 * 100, 2)

        high_60 = max(highs[-60:]) if len(highs) >= 60 else max(highs)
        bias_60 = 0
        if ma60 > 0: bias_60 = round((latest['close'] - ma60) / ma60 * 100, 1)

        is_squeeze = False
        if ma5 > 0 and ma20 > 0 and ma60 > 0:
            mas = [ma5, ma20, ma60]
            if (max(mas) - min(mas)) / min(mas) < 0.03: is_squeeze = True

        return {
            "code": stock_id, 
            "close": latest['close'], 
            "ma5": ma5, "ma20": ma20, "ma60": ma60,
            "slope_ma20": slope_ma20,
            "high_60": high_60,
            "bias_60": bias_60,
            "is_squeeze": is_squeeze,
            "volatility": round((latest['max'] - latest['min']) / latest['close'] * 100, 1) if latest['close'] > 0 else 0
        }
    except: return None

def fetch_chips_accumulate(stock_id):
    token = os.environ.get('FINMIND_TOKEN', '')
    url = "https://api.finmindtrade.com/api/v4/data"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        start = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d')
        res = requests.get(url, params={"dataset": "TaiwanStockInstitutionalInvestorsBuySell", "data_id": stock_id, "start_date": start, "token": token}, headers=headers, timeout=5)
        data = res.json().get('data', [])
        if not data: return 0, 0, 0, 0
        
        latest_date = data[-1]['date']
        today_f = 0; today_t = 0
        unique_dates = sorted(list(set([d['date'] for d in data])), reverse=True)[:5]
        acc_f = 0; acc_t = 0
        
        for row in data:
            if row['date'] in unique_dates:
                val = row['buy'] - row['sell']
                if row['name'] == 'Foreign_Investor':
                    acc_f += val
                    if row['date'] == latest_date: today_f = val
                elif row['name'] == 'Investment_Trust':
                    acc_t += val
                    if row['date'] == latest_date: today_t = val
        return int(today_f/1000), int(today_t/1000), int(acc_f/1000), int(acc_t/1000)
    except: return 0, 0, 0, 0

def fetch_full_data(stock_id
