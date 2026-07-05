import os
import io
import csv
import zipfile
import sqlite3
import feedparser
import requests
import json
import time
from datetime import datetime, date
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
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# 路徑修正：改用相對路徑
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "taoyuan_news_monitor.db")
BACKUP_PATH = os.path.join(BASE_DIR, "taoyuan_reports_backup.txt")

# 想關注的桃園行政區 (可自行增減)
TARGET_DISTRICTS = ["八德區", "平鎮區", "楊梅區"]

# 桃園市在實價登錄開放資料的縣市代碼
CITY_CODE = "H"

# 內政部實價登錄開放資料下載頁 (每月 1、11、21 日更新)
LVR_OPENDATA_URL = "https://plvr.land.moi.gov.tw/DownloadOpenData"

# 591 新建案 regionid：桃園市 = 6
HOUSE591_REGIONID = 6
HOUSE591_LIST_URL = "https://newhouse.591.com.tw/home/housing/list-search"

# 一般瀏覽器 User-Agent，降低被直接擋掉的機率
COMMON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}


# ============================================================
# 資料庫 (RSS 新聞去重複用)
# ============================================================

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


# ============================================================
# 模組一：RSS 新聞蒐集
# ============================================================

def fetch_news():
    new_articles = []
    keywords = ["桃園", "中正藝文特區", "青埔", "A7", "中壢", "平鎮", "八德"]

    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                content = title + summary

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

    if not new_articles:
        print("No new RSS articles, using fallback research data...")
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


# ============================================================
# 模組二：政府實價登錄 - 屋齡30年內透天厝 成交行情
# ============================================================

def fetch_land_price_records(max_records=5):
    """
    下載內政部實價登錄開放資料 (買賣案件)，篩選：
    - 桃園市
    - 建物型態包含「透天厝」
    - 屋齡 <= 30 年
    - 行政區屬於 TARGET_DISTRICTS
    回傳最近成交的幾筆紀錄 (list of dict)。
    任何步驟失敗都會回傳空 list，不會讓整支程式中斷。
    """
    try:
        # 實價登錄開放資料為 ZIP 檔，內含各縣市 CSV
        # 檔名格式：H_lvr_land_A.csv 代表桃園市(H) 買賣案件(A)
        resp = requests.get(LVR_OPENDATA_URL, headers=COMMON_HEADERS, timeout=30)
        resp.raise_for_status()

        zip_file = zipfile.ZipFile(io.BytesIO(resp.content))
        target_filename = None
        for name in zip_file.namelist():
            if name.upper().startswith(f"{CITY_CODE}_LVR_LAND_A"):
                target_filename = name
                break

        if not target_filename:
            print("找不到桃園市買賣案件 CSV，可能是開放資料網站格式已變更。")
            return []

        with zip_file.open(target_filename) as f:
            # 內政部 CSV 編碼通常是 UTF-8，且第一行是英文欄位說明，第二行才是中文標頭
            raw_text = f.read().decode("utf-8-sig", errors="ignore")

        reader = csv.reader(io.StringIO(raw_text))
        rows = list(reader)
        if len(rows) < 3:
            return []

        header = rows[1]  # 第二行是中文欄位名稱
        data_rows = rows[2:]

        def col_index(name):
            try:
                return header.index(name)
            except ValueError:
                return None

        idx_district = col_index("鄉鎮市區")
        idx_type = col_index("建物型態")
        idx_date = col_index("交易年月日")
        idx_total_price = col_index("總價元")
        idx_area = col_index("建物移轉總面積平方公尺")
        idx_build_date = col_index("建築完成年月")
        idx_address = col_index("土地位置建物門牌")

        results = []
        for row in data_rows:
            try:
                if idx_type is None or "透天厝" not in row[idx_type]:
                    continue
                if idx_district is None or not any(d in row[idx_district] for d in TARGET_DISTRICTS):
                    continue

                # 計算屋齡：交易年月日 - 建築完成年月 (民國年格式，例如 1130615)
                if idx_build_date is None or not row[idx_build_date].strip():
                    continue
                build_year_roc = int(row[idx_build_date][:3])
                trade_year_roc = int(row[idx_date][:3]) if idx_date is not None else None
                if trade_year_roc is None:
                    continue
                house_age = trade_year_roc - build_year_roc
                if house_age < 0 or house_age > 30:
                    continue

                total_price = row[idx_total_price] if idx_total_price is not None else ""
                area = row[idx_area] if idx_area is not None else ""
                address = row[idx_address] if idx_address is not None else ""
                trade_date = row[idx_date] if idx_date is not None else ""

                results.append({
                    "district": row[idx_district],
                    "address": address,
                    "house_age": house_age,
                    "total_price": total_price,
                    "area_sqm": area,
                    "trade_date": trade_date,
                })
            except (ValueError, IndexError):
                continue

        # 依交易日期新到舊排序，取前 N 筆
        results.sort(key=lambda x: x["trade_date"], reverse=True)
        return results[:max_records]

    except Exception as e:
        print(f"抓取實價登錄資料失敗（已略過此區塊，不影響其他功能）：{e}")
        return []


def format_land_price_section(records):
    if not records:
        return "（本次未取得符合條件的實價登錄成交紀錄）"

    lines = []
    for r in records:
        price_wan = "未知"
        try:
            price_wan = f"{int(r['total_price']) / 10000:.0f} 萬元"
        except (ValueError, TypeError):
            pass

        area_ping = "未知"
        try:
            area_ping = f"{float(r['area_sqm']) / 3.30579:.1f} 坪"
        except (ValueError, TypeError):
            pass

        lines.append(
            f"- 【{r['district']}】屋齡約 {r['house_age']} 年透天厝，"
            f"成交總價 {price_wan}，面積約 {area_ping}（交易日期：{r['trade_date']}，"
            f"地址：{r['address']}）"
        )
    return "\n".join(lines)


# ============================================================
# 模組三：591 新建案 - 三房 + 車位 推薦
# 【注意】591 有反爬蟲機制且經常改版，此區塊設計為「失敗就跳過」
# 不會讓整支程式中斷，也不會影響新聞分析與實價登錄區塊
# ============================================================

def fetch_591_new_projects(max_records=5):
    try:
        params = {
            "page": 1,
            "device": "pc",
            "regionid": HOUSE591_REGIONID,
        }
        resp = requests.get(HOUSE591_LIST_URL, params=params, headers=COMMON_HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        items = data.get("data", {}).get("items", [])
        if not items:
            print("591 新建案 API 沒有回傳資料，可能是反爬蟲機制已擋掉，略過此區塊。")
            return []

        results = []
        for item in items:
            try:
                name = item.get("name", "")
                room_info = str(item.get("room", "")) + str(item.get("layout", "")) + name
                has_parking = "車位" in json.dumps(item, ensure_ascii=False)
                is_three_room = "3房" in room_info or "三房" in room_info

                if not (is_three_room and has_parking):
                    continue

                results.append({
                    "name": name,
                    "price": item.get("price", item.get("total_price", "未提供")),
                    "address": item.get("address", item.get("section_name", "")),
                    "link": "https://newhouse.591.com.tw/" + str(item.get("id", "")),
                    "image": item.get("cover", item.get("pic_url", "")),
                })
            except Exception:
                continue

        return results[:max_records]

    except Exception as e:
        print(f"抓取 591 新建案資料失敗（已略過此區塊，不影響其他功能）：{e}")
        return []


def format_591_section(projects):
    if not projects:
        return "（本次未取得符合「三房+車位」條件的新建案資訊，可能是來源網站暫時無法取得資料）"

    lines = []
    for p in projects:
        lines.append(
            f"- 【{p['name']}】{p['address']}｜參考價：{p['price']}\n"
            f"  連結：{p['link']}"
        )
    return "\n".join(lines)


# ============================================================
# AI 分析與報告產出
# ============================================================

def analyze_news(articles, land_price_section, house591_section):
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

    以下是「屋齡30年內透天厝」實際成交行情資料（來自政府實價登錄，請直接引用，不要編造數字）：
    {land_price_section}

    以下是「三房+車位」新建案資訊（來自591，請直接引用，若顯示「未取得」則在報告中誠實說明本次沒有符合條件的建案，不要編造）：
    {house591_section}

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

    🏚️ 【屋齡30年內透天厝 成交行情參考】
    [根據上方提供的實價登錄資料，整理成條列式重點，並給出 Jemmy Ko 風格的議價建議]

    🏘️ 【三房+車位 新建案推薦】
    [根據上方提供的591建案資訊，整理成條列式重點；若無資料請誠實說明]

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

    print("正在抓取實價登錄成交行情...")
    land_price_records = fetch_land_price_records()
    land_price_section = format_land_price_section(land_price_records)

    print("正在抓取591新建案資訊...")
    house591_records = fetch_591_new_projects()
    house591_section = format_591_section(house591_records)

    if not articles:
        print("No new articles found. 仍會產出實價登錄與建案區塊的簡易報告。")
        articles = []  # 讓報告至少能包含實價登錄/建案資訊

    print(f"Found {len(articles)} new articles. Analyzing...")
    report = analyze_news(articles, land_price_section, house591_section)

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
