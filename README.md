# 桃園房地產市場分析器 (Taoyuan Real Estate Analyzer)

這是一個自動化的桃園房地產市場新聞分析系統。它會定期從多個來源抓取最新的房市新聞，使用 AI (OpenAI) 進行深度分析，並將分析報告發送到指定的 Discord 頻道。

## 功能特點

- **多來源抓取**：整合多個房產新聞 RSS 來源。
- **AI 深度分析**：模擬專業分析師風格，提供具備洞察力的市場報告。
- **Discord 推播**：自動將報告發送至 Discord 頻道。
- **本地備份**：所有生成的報告都會備份在本地文件中。
- **去重機制**：使用 SQLite 資料庫確保新聞不重複處理。

## 安裝與設定

### 1. 複製專案
```bash
git clone https://github.com/您的用戶名/taoyuan-real-estate-analyzer.git
cd taoyuan-real-estate-analyzer
```

### 2. 安裝依賴
```bash
pip install feedparser requests openai
```

### 3. 設定環境變數
您需要設定以下環境變數：
- `OPENAI_API_KEY`: 您的 OpenAI API 金鑰。
- `DISCORD_WEBHOOK_URL`: 您的 Discord Webhook URL。

### 4. 執行程式
```bash
python taoyuan_real_estate_analyzer.py
```

## 檔案說明
- `taoyuan_real_estate_analyzer.py`: 主程式碼。
- `taoyuan_news_monitor.db`: 新聞去重資料庫 (執行後產生)。
- `taoyuan_reports_backup.txt`: 報告備份檔 (執行後產生)。

## 授權
MIT License
