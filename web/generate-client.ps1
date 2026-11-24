$ErrorActionPreference = 'Stop'
Push-Location (Join-Path $PSScriptRoot '..')
try {
    $env:PYTHONPATH = 'backend'
    & .\.venv\Scripts\python.exe -m viora.export_contracts
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
    # Use a provisioned openapi-typescript 7.x CLI. No dependency installation occurs here.
    & openapi-typescript contracts/http/openapi.json -o web/api.generated.ts
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
