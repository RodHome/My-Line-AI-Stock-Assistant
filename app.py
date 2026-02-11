import os, requests, random, re
import json
import time
import math
import concurrent.futures
import twstock
import psutil  # 🚀 新增：資源監控
import gc      # 🚀 新增：垃圾回收控制
from datetime import datetime, timedelta, time as dtime, timezone
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FlexSendMessage

app = Flask(__name__)

# 🟢 [版本號] v15.6.2 (Resource Optimized)
BOT_VERSION = "v15.6.2"

# --- [新增] 資源監控與自動回收工具 ---
def get_memory_usage():
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / 1024 / 1024  # MB

def resource_guard(func):
    """裝飾器：監控記憶體並在結束後強制回收"""
    def wrapper(*args, **kwargs):
        mem_before = get_memory_usage()
        try:
            res = func(*args, **kwargs)
            return res
        finally:
            mem_after = get_memory_usage()
            # 在 Log 中輸出資源變化
            print(f"--- [Resource Log] {func.__name__} | Memory: {mem_before:.1f}MB -> {mem_after:.1f}MB ---")
            # 如果記憶體佔用過高 (例如超過 700MB)，主動清理
            if mem_after > 700:
                gc.collect()
    return wrapper

# --- 1. 全域快取與設定 ---
# 🚀 優化建議：快取加上上限，防止字典無限增長
AI_RESPONSE_CACHE = {}
MAX_CACHE_SIZE = 50 

# (ETF_META 與 ELITE_STOCK_DATA 保持不變...)
ETF_META = {
    "00878": {"name": "國泰永續高股息", "type": "高股息", "focus": "ESG/殖利率/填息"},
    "0056":  {"name": "元大高股息", "type": "高股息", "focus": "預測殖利率/填息"},
    # ... (其餘省略，保持你原本的內容)
}

ELITE_STOCK_DATA = {
    "台積電": {"code": "2330", "sector": "半導體/晶圓代工"},
    # ... (其餘省略，保持你原本的內容)
}
ELITE_STOCK_POOL = {k: v["code"] for k, v in ELITE_STOCK_DATA.items()}
ALL_STOCK_MAP = ELITE_STOCK_POOL.copy()

# (外部名單載入邏輯保持不變...)
try:
    if os.path.exists('stock_list.json'):
        with open('stock_list.json', 'r', encoding='utf-8') as f:
            full_list = json.load(f)
            ALL_STOCK_MAP.update(full_list)
            print(f"[System] 外部名單載入成功。")
except Exception as e:
    print(f"[System] 使用內建名單: {e}")

CODE_TO_NAME = {v: k for k, v in ALL_STOCK_MAP.items()}

# Line API 設定
token = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
secret = os.environ.get('LINE_CHANNEL_SECRET')
line_bot_api = LineBotApi(token if token else 'UNKNOWN')
handler = WebhookHandler(secret if secret else 'UNKNOWN')

# --- 2. 核心引擎 (保持邏輯，加上資源保護) ---

@app.route("/")
def health_check():
    return f"OK ({BOT_VERSION}) | Mem: {get_memory_usage():.1f}MB", 200

# (calculate_rsi, calculate_kd, calculate_cdp, get_technical_signals 保持不變...)

# --- 3. 智慧快取與 API 優化 ---
def set_cached_ai_response(key, data):
    # 🚀 防止快取爆炸
    if len(AI_RESPONSE_CACHE) > MAX_CACHE_SIZE:
        AI_RESPONSE_CACHE.clear() 
    AI_RESPONSE_CACHE[key] = {'data': data, 'expires': time.time() + get_smart_cache_ttl()}

@resource_guard
def scan_recommendations_turbo(target_sector=None):
    candidates = []
    if target_sector:
        pool = [v['code'] for k, v in ELITE_STOCK_DATA.items() if target_sector in v['sector']]
        sample_list = pool if pool else []
    else:
        elite_codes = list(ELITE_STOCK_POOL.values())
        # 🚀 減少單次掃描樣本數，從 25 降至 15，減輕記憶體壓力
        sample_list = random.sample(elite_codes, 15) if len(elite_codes) > 15 else elite_codes
    
    # 🚀 將 max_workers 調降至 4，防止在 1GB 限制下產生過多暫存物件
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        results = executor.map(check_stock_worker_turbo, sample_list)
    
    for res in results:
        if res: candidates.append(res)
        if len(candidates) >= 3: break
    return candidates

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
@resource_guard # 🚀 每個訊息處理完都檢查記憶體
def handle_message(event):
    msg = event.message.text.strip()
    
    # [功能 1] 推薦選股
    msg_parts = msg.split()
    if msg_parts[0] in ["推薦", "選股"]:
        target_sector = msg_parts[1] if len(msg_parts) > 1 else None
        good_stocks = scan_recommendations_turbo(target_sector)
        
        if not good_stocks:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 掃描後目前無符合標的。"))
            return
            
        # (AI 選股邏輯與 Flex Message 保持不變...)
        # ... 原有代碼 ...
        
        # ⚠️ 注意：在使用完大型變數（如 good_stocks）後，若後續還有長邏輯，可考慮在此之後執行 gc.collect()
        return

    # [功能 2] 個股/ETF 診斷
    # ... (其餘邏輯保持不變)
    # 註：建議在 handle_message 最後不需要寫 gc.collect()，因為 @resource_guard 會處理。

# (fetch_data_light 等資料抓取函數保持不變...)

if __name__ == "__main__":
    # 強制執行一次清理
    gc.collect()
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
