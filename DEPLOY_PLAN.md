# DEPLOY_PLAN

## 目標
- 部署可公開連線的 Age Kiosk 雲端展示版
- 保留本機版核心邏輯，不做大改架構

## 一天執行版流程

1. 建立雲端服務（Render Web Service）
2. 設定 Build/Start 指令
3. 設定環境變數（至少 `AGE_KIOSK_CLOUD_MODE=1`）
4. 首次部署並檢查 `/api/v1/cloud/health`
5. 用手機/筆電開網頁測試攝影機授權與推論回傳
6. 截圖與錄影保留部署證據（供履歷與作品集）

## Render 參數
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn wsgi:app --workers 1 --threads 4 --timeout 120`

## 驗證清單
- `GET /api/v1/cloud/health` 回 200
- 首頁能開啟瀏覽器鏡頭
- `POST /api/v1/cloud/infer` 有回 provider / decision_code
- 無 GPU 環境時可自動 fallback 到 CPU

## 已知限制
- 雲端模式目前為「單張影格推論回傳」，不等同原本全狀態機流程
- 首次 InsightFace 初始化較慢屬正常
- 攝影機權限建議使用 HTTPS 網域測試
