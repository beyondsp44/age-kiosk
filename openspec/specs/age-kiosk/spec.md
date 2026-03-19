# age-kiosk 規格（Deploy 版）

## 目的

Age Kiosk Deploy 提供雙模式：
- 本機模式：維持原本 `cv2.VideoCapture` 即時驗證流程
- 雲端模式：由瀏覽器提供鏡頭影像，後端執行年齡推論並回傳分流結果

## Requirements

### Requirement: 雙模式啟動
系統 SHALL 透過環境變數切換本機模式與雲端模式。

#### Scenario: 本機模式
- GIVEN `AGE_KIOSK_CLOUD_MODE=0`（或未設定）
- WHEN 啟動 `app.py`
- THEN 應維持既有本機攝影機流程

#### Scenario: 雲端模式
- GIVEN `AGE_KIOSK_CLOUD_MODE=1`
- WHEN 啟動 `app.py`
- THEN 根路由應提供雲端鏡頭展示頁
- AND 後端不應強制初始化本機攝影機引擎

### Requirement: 雲端推論 API
系統 SHALL 提供可部署的影像推論 API，供瀏覽器或外部客戶端呼叫。

#### Scenario: multipart 影像推論
- GIVEN 呼叫 `POST /api/v1/cloud/infer`
- WHEN 請求包含欄位 `image`
- THEN 系統應回傳年齡推論與分流結果

#### Scenario: base64 影像推論
- GIVEN 呼叫 `POST /api/v1/cloud/infer`
- WHEN JSON 含 `image_base64`
- THEN 系統應回傳年齡推論與分流結果

#### Scenario: 無臉或資料錯誤
- GIVEN 推論資料無法使用
- WHEN API 完成處理
- THEN 應回傳對應錯誤訊息與狀態碼

### Requirement: Provider 自動 fallback
系統 SHALL 優先使用可用 GPU provider，失敗時退回 CPU。

#### Scenario: provider 可查詢
- GIVEN 呼叫 `GET /api/v1/cloud/health`
- WHEN API 回傳
- THEN 應包含 `available_providers` 與 `provider_selected`

### Requirement: 保守式分流規則
系統 SHALL 維持保守式分流原則。

#### Scenario: 未成年
- GIVEN AI 年齡小於等於 15
- WHEN 推論完成
- THEN `decision_code` 應為 `FAIL_MINOR`

#### Scenario: 灰區
- GIVEN AI 年齡介於 16 到 27
- WHEN 推論完成
- THEN `decision_code` 應為 `GREY_VERIFY`
- AND `needs_manual_verify` 應為 `true`

#### Scenario: 成年
- GIVEN AI 年齡大於等於 28
- WHEN 推論完成
- THEN `decision_code` 應為 `PASS_ADULT`

### Requirement: 隱私保護
系統 SHALL 不保存原始影像。

#### Scenario: 請求完成後
- GIVEN API 推論已完成
- WHEN 檢查資料保存
- THEN 系統僅回傳結果，不持久化原始影像
