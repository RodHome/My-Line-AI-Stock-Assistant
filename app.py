import sys
import os
from flask import Flask

app = Flask(__name__)

@app.route("/")
def version_check():
    # 這行指令會回傳詳細的 Python 版本資訊
    version_info = sys.version
    return f"""
    <h1>Zeabur 環境診斷報告</h1>
    <p><strong>目前的 Python 版本：</strong></p>
    <pre>{version_info}</pre>
    """

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
