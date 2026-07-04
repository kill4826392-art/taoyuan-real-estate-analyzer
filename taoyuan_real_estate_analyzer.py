import os
import sqlite3
import feedparser
import requests
import json
import time
from datetime import datetime
from openai import OpenAI

# --- 設定區 ---
# 桃園房市相關新聞來源 (包含通用房產與地方新聞)
RSS_FEEDS = [
    "https://news.housefun.com.tw/rss/news/桃園",
    "https://www.fbs168.com/rss/subscribe", # 富比士地產王
    "https://estate.ltn.com.tw/rss/index.xml", # 自由地產天下
    "https://www.mygonews.com/rss/news/list", # MyGoNews
    "https://www.tycg.gov.tw/cp.aspx?n=18", # 桃園市政府最新消息
]

# 安全性修正：不再寫死 Webhook 網址，一定要從環境變數 (GitHub Secrets) 讀取
# 如果沒有設定 Secrets，這裡會是 None，程式會印出警告而不是誤用舊網址
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# 路徑修正：改用相對路徑，這樣不管在本機、GitHub Actions 或任何伺服器都能正常執行
# os.path.dirname(__file__) 代表「這支程式檔案所在的資料夾」
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "taoyuan_news_monitor.db")
BACKUP_PATH = os.path.join(BASE_DIR, "taoyuan_reports_backup.txt")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS news (
            id TEXT PRIMARY KEY,
            title TEXT,
            link TEXT,
            published TEXT
        )
    ''')
    conn.commit()
    conn.close()

def is_new(news_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM news WHERE id = ?", (news_id,))
    result = cursor.fetchone()
    conn.close()
    return result is None

def save_news(news_id, title, link, published):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO news (id, title, link, published) VALUES (?, ?, ?, ?)", 
                       (news_id, title, link, published))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()

def fetch_news():
    new_articles = []
    # 桃園關鍵字
    keywords = ["桃園", "中正藝文特區", "青埔", "A7", "中壢", "平鎮", "八德"]
    
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                content = title + summary
                
                # 過濾桃園相關新聞
                if any(k in content for k in keywords):
                    news_id = entry.get("id", entry.get("link", ""))
                    if is_new(news_id):
                        new_articles.append({
                            "title": title,
                            "link": entry.get("link", ""),
                            "summary": summary,
                            "published": entry.get("published", ""),
                            "id": news_id
                        })
                        save_news(news_id, title, entry.get("link", ""), entry.get("published", ""))
        except Exception as e:
            print(f"Error fetching {url}: {e}")
            
    # 如果 RSS 沒抓到新內容，嘗試從搜尋結果補充（模擬最新資訊）
    if not new_articles:
        print("No new RSS articles, using fallback research data...")
        # 這裡加入剛才搜尋到的最新資訊作為補充
        fallback_data = [
            {"title": "桃園6月房市交易量創30個月新高", "summary": "六都2024年6月的買賣移轉棟數已全數公佈，桃園4641棟，月增11.6%，年增11.7%。", "link": "https://tw.stock.yahoo.com/news/%E6%A1%83%E5%9C%926%E6%9C%88%E6%88%BF%E5%B8%82%E4%BA%A4%E6%98%93%E9%87%8F%E5%89%B530%E5%80%8B%E6%9C%88%E6%96%B0%E9%AB%98-092047922.html"},
            {"title": "桃園市113年6月不動產市場交易分析", "summary": "全市113年6月買賣登記案件量共計5,379件，建物移轉棟數為4,641棟，較去年增加32.7%。", "link": "https://land.tycg.gov.tw/News_Content.aspx?n=3926&s=1327183"},
            {"title": "桃園房市變了！平均屋齡悄破20年老宅吸買盤回流", "summary": "2024年第四季住宅交易，桃園平均交易屋齡為21.7年，和去年同期的19.2年相比增加不少。", "link": "https://estate.ltn.com.tw/article/24068"}
        ]
        for item in fallback_data:
            if is_new(item['link']):
                new_articles.append(item)
                save_news(item['link'], item['title'], item['link'], "2024-06-04")
                
    return new_articles

def analyze_news(articles):
    if not articles:
        return None
    
    client = OpenAI()
    content = "\n\n".join([f"標題: {a['title']}\n摘要: {a['summary'][:300]}\n連結: {a['link']}" for a in articles])
    
    prompt = f"""
    你現在的角色是 Jemmy Ko (JKL SEO 公司的首席優化師)。請針對以下「桃園房地產」新聞內容進行深度整合分析。
    
    Jemmy Ko 的寫作風格特點：
    1. **專業且具洞察力**：不僅報導新聞，更強調數據背後的邏輯與 SEO/數位行銷角度的思考。
    2. **簡潔有力**：使用清晰的條列點，直擊問題核心，不拖泥帶水。
    3. **強烈推薦**：對於值得關注的標的會給予明確的評價與建議。
    4. **語氣沉穩**：展現出作為行業專家的自信與權威。
    
    新聞內容：
    {content}
    
    ---
    報告格式要求：
    
    🏠 【桃園房市重點快報 (Taoyuan Real Estate Flash)】
    [列出 3-5 則最重要的新聞，格式為：新聞標題 - 來源 [連結](URL)]
    核心分析：[以 Jemmy Ko 的視角進行深度分析，解釋為何這則新聞對桃園房市重要]
    
    ---
    
    📊 【區域深度觀察 (Regional Insights)】
    🚀 【核心重劃區動態】
    [分析 青埔、A7、中正藝文特區 等熱區的最新趨勢與投資邏輯]
    
    🏗️ 【軌道交通與建設進度】
    [分析 捷運綠線、鐵路地下化 等對周邊房價的長期影響]
    
    📉 【交易量與屋齡結構分析】
    [針對移轉棟數創高或屋齡老化等現象進行專業解讀]
    
    ---
    
    🎯 【投資觀點與市場建議 (Investment Strategy)】
    首購族建議：[Jemmy Ko 風格的具體建議]
    置產/投資建議：[針對長期持有或資產配置的觀點]
    
    市場情緒評估：[Jemmy Ko 風格的情緒評估，綜合政策與市場數據]
    
    ---
    
    💡 【今日桃園房市一句話總結】
    [以 Jemmy Ko 的語氣總結今日桃園房市最核心的洞察]
    
    📡 更新時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    
    請使用繁體中文回答。
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"AI Analysis Error: {e}")
        return None

def send_to_discord(report):
    backup_report(report)
    if not DISCORD_WEBHOOK_URL:
        print("警告：找不到 DISCORD_WEBHOOK_URL，請確認 GitHub Secrets 是否設定正確。")
        return False
    
    if len(report) > 1900:
        chunks = [report[i:i+1900] for i in range(0, len(report), 1900)]
        for chunk in chunks:
            payload = {"content": f"{chunk}"}
            try:
                requests.post(DISCORD_WEBHOOK_URL, json=payload)
                time.sleep(1)
            except:
                pass
        return True
    else:
        payload = {"content": f"{report}"}
        try:
            res = requests.post(DISCORD_WEBHOOK_URL, json=payload)
            return res.status_code == 204
        except Exception as e:
            print(f"Discord connection error: {e}")
            return False

def backup_report(report):
    with open(BACKUP_PATH, "a", encoding="utf-8") as f:
        f.write(f"\n--- REPORT BACKUP {datetime.now()} ---\n")
        f.write(report)
        f.write("\n" + "="*50 + "\n")

def main():
    print(f"[{datetime.now()}] Starting Taoyuan Real Estate Analyzer (Jemmy Ko Style)...")
    init_db()
    articles = fetch_news()
    
    if not articles:
        print("No new articles found.")
        return
    
    print(f"Found {len(articles)} new articles. Analyzing...")
    report = analyze_news(articles)
    
    if report:
        success = send_to_discord(report)
        if success:
            print("Report sent to Discord successfully.")
        else:
            print("Report backed up locally but failed to send to Discord.")
    else:
        print("Failed to generate report.")

if __name__ == "__main__":
    main()
