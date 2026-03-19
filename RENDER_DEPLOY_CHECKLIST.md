# Render Deploy Checklist

## 0) Scope
- This checklist is for `Age_kiosk_deploy` only.
- Do not apply it to local stable folder `Age_kiosk`.

## 1) Push latest deploy branch
```powershell
cd "F:\Nice workspace\Age_kiosk_deploy"
git add .
git commit -m "prepare render deploy checklist and config"
git push
```

## 2) Create Web Service on Render
1. Open Render Dashboard.
2. Click `New +` -> `Web Service`.
3. Connect repo: `beyondsp44/age-kiosk`.
4. Branch: `main`.
5. Runtime: `Python 3` (runtime is pinned by `runtime.txt`).

## 3) Service settings
- Build Command
```bash
pip install -r requirements.txt
```

- Start Command
```bash
gunicorn wsgi:app --workers 1 --threads 4 --timeout 120
```

## 4) Required environment variables
- `AGE_KIOSK_CLOUD_MODE=1`
- `AGE_KIOSK_QUIET_HTTP_LOGS=1`
- `AGE_KIOSK_CLOUD_MAX_IMAGE_MB=5`
- `AGE_KIOSK_CLOUD_INFER_INTERVAL_MS=1200`

Optional:
- `AGE_KIOSK_SUPABASE_URL`
- `AGE_KIOSK_SUPABASE_API_KEY`
- `AGE_KIOSK_SUPABASE_TABLE=detection_logs`
- `AGE_KIOSK_SUPABASE_TIMEOUT_SEC=2.0`

## 5) Post-deploy verification
1. Open `https://<your-render-domain>/api/v1/cloud/health`
2. Confirm JSON has:
   - `success: true`
   - `data.provider_selected`
3. Open `https://<your-render-domain>/`
4. Allow browser camera permission.
5. Click `Start Camera` and check inference updates.

## 6) Expected behavior
- If GPU is unavailable on Render free instance, provider should fallback to CPU.
- First inference can be slower because model runtime initializes.
- Service may sleep on free plan and wake up on first request.

## 7) Fast rollback
- In Render, open the previous successful deploy and click rollback.
