def ask_gemini(prompt):
    import google.generativeai as genai
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        return "錯誤：找不到 GEMINI_API_KEY"
    
    try:
        genai.configure(api_key=api_key)
        
        # 嘗試使用最穩定的名稱格式
        try:
            model = genai.GenerativeModel('gemini-1.5-flash')
            response = model.generate_content(prompt)
        except:
            # 如果失敗，嘗試加上完整路徑格式
            model = genai.GenerativeModel('models/gemini-1.5-flash')
            response = model.generate_content(prompt)
            
        return response.text
    except Exception as e:
        return f"AI 最終報錯：{str(e)}。請確認 API Key 是否來自 Google AI Studio 且具備 Gemini 1.5 權限。"
