# VIORA-AI Phase 1 Browser Acceptance Tests

`acceptance.mjs` executes isolated headless Chromium verification against a running local backend (`uvicorn` on `127.0.0.1:8000`) and Vite frontend (`127.0.0.1:5173`). It uses an ephemeral browser context without touching any personal browser profile.

## Prerequisites

1. Start PostgreSQL, backend, and Vite dev server:
   - Backend (plain HTTP local cookies require `AUTH_COOKIE_SECURE=false` in-process):
     ```powershell
     $env:AUTH_COOKIE_SECURE = "false"
     .\.venv\Scripts\uvicorn.exe viora.main:create_app --factory --app-dir backend --host 127.0.0.1 --port 8000
     ```
   - Frontend:
     ```powershell
     cd web
     npm run dev -- --host 127.0.0.1 --port 5173
     ```
2. Install `playwright-core` in a temporary directory outside the repository (to avoid modifying `web/package.json`), or point `NODE_PATH` to a global/external `playwright-core` installation:
   ```powershell
   mkdir $env:TEMP\viora-pw -Force
   pushd $env:TEMP\viora-pw
   npm init -y | Out-Null
   npm install --no-audit --no-fund playwright-core
   popd
   ```

## Execution

Set `VIORA_TEST_PASSWORD` to the password configured for the synthetic dev accounts, and run:

```powershell
$env:NODE_PATH = "$env:TEMP\viora-pw\node_modules"
$env:VIORA_TEST_PASSWORD = "<your-local-dev-password>"
# Optional: $env:VIORA_SCREENSHOT_DIR = "$env:TEMP\viora-screenshots"
# Optional: $env:PLAYWRIGHT_EXECUTABLE_PATH = "C:\Program Files\Google\Chrome\Application\chrome.exe"
node web\e2e\acceptance.mjs
```

All browser cookies and contexts are ephemeral, and the script verifies that `localStorage` and `sessionStorage` remain empty across all flows.
