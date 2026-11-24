$ErrorActionPreference = 'Stop'
Push-Location (Join-Path $PSScriptRoot '..')
try {
    $env:PYTHONPATH = 'backend'
    & .\.venv\Scripts\python.exe -m viora.export_contracts
    if ($LASTEXITCODE) { exit $LASTEXITCODE }
    # Use pinned openapi-typescript CLI via npx in isolated execution
    & npx --yes openapi-typescript@7.6.1 contracts/http/openapi.json -o web/src/api/generated.ts
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
