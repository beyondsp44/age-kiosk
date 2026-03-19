# Age Kiosk Deploy

Age Kiosk Deploy 是雲端部署分支，保留原本本機流程，同時新增 `CLOUD_MODE` 供遠端瀏覽器攝影機推論使用。

## 本輪改動重點
- 新增雲端推論 API：`/api/v1/cloud/infer`
- 新增雲端健康檢查 API：`/api/v1/cloud/health`
- 新增雲端展示頁（瀏覽器鏡頭）：`/`（當 `AGE_KIOSK_CLOUD_MODE=1`）
- 保留原本本機模式（`AGE_KIOSK_CLOUD_MODE=0`）

## 兩種運行模式

### 1) 本機模式（預設）
- 使用 `cv2.VideoCapture` 讀本機鏡頭
- 適合單機展示、實體現場

### 2) 雲端模式（新增）
- 瀏覽器取得使用者鏡頭影像
- 前端週期上傳影格到 `/api/v1/cloud/infer`
- 後端回傳年齡估測與分流決策

## 快速啟動（本地測 deploy 版）

```powershell
cd "F:\Nice workspace\Age_kiosk_deploy"
python -m pip install -r requirements.txt
$env:AGE_KIOSK_CLOUD_MODE="1"
python .\app.py
```

開啟：`http://127.0.0.1:5000`

## Render 部署（建議）

### Build Command
```bash
pip install -r requirements.txt
```

### Start Command
```bash
gunicorn wsgi:app --workers 1 --threads 2 --timeout 600 --max-requests 50 --max-requests-jitter 10
```

### 必要環境變數
- `PYTHON_VERSION=3.10.13`
- `AGE_KIOSK_CLOUD_MODE=1`
- `AGE_KIOSK_QUIET_HTTP_LOGS=1`
- `AGE_KIOSK_CLOUD_MAX_IMAGE_MB=5`
- `AGE_KIOSK_CLOUD_INFER_INTERVAL_MS=1200`
- `AGE_KIOSK_CLOUD_MODEL_NAME=buffalo_s`
- `OMP_NUM_THREADS=1`
- `OPENBLAS_NUM_THREADS=1`
- `MKL_NUM_THREADS=1`
- `NUMEXPR_NUM_THREADS=1`

可直接使用 repo 內的 `render.yaml` 當設定基準，避免手動漏填。

可選（有接 Supabase 才設）
- `AGE_KIOSK_SUPABASE_URL`
- `AGE_KIOSK_SUPABASE_API_KEY`
- `AGE_KIOSK_SUPABASE_TABLE=detection_logs`
- `AGE_KIOSK_SUPABASE_TIMEOUT_SEC=2.0`

## Cloud API

### GET `/api/v1/cloud/health`
回傳可用 provider 與 runtime 狀態。

### POST `/api/v1/cloud/infer`
支援兩種輸入：
- `multipart/form-data`：欄位 `image`
- JSON：`{"image_base64": "..."}`

回傳欄位包含：
- `provider`
- `face_count`
- `ai_age`
- `ai_age_range`
- `decision_code`
- `decision_label`
- `needs_manual_verify`

## 注意事項
- 若沒有 NVIDIA CUDA 可用，雲端會自動使用 CPU provider。
- 首次推論會初始化 InsightFace，可能較慢。
- 雲端模式要用 HTTPS 網域，瀏覽器才會穩定允許攝影機權限。
- 此分支不內建 `.venv`；請在部署端安裝依賴。
- Deploy 分支使用 `opencv-python-headless`，避免雲端環境缺少 GUI 動態函式庫。
- 首次推論可能需要下載與初始化模型，若遇到暫時連線錯誤，通常重試即可。
