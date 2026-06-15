<#
.SYNOPSIS
  One-shot environment bootstrapper for the Smart Appointment AI Agent (Windows).

.DESCRIPTION
  - Validates Python >= 3.10
  - Creates / reuses .venv
  - Installs requirements.txt
  - Scaffolds .env from .env.example
  - Ensures data/ directory exists
  - Runs verify_env.py
  - Optionally launches uvicorn

.PARAMETER Force
  Recreate the .venv from scratch.

.PARAMETER Run
  After setup, launch `uvicorn app:app` on 127.0.0.1:8001.

.PARAMETER NoVerify
  Skip the verify_env.py import smoke test.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .github\skills\setup-environment\scripts\setup.ps1
  powershell -ExecutionPolicy Bypass -File .github\skills\setup-environment\scripts\setup.ps1 -Force -Run
#>
[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$Run,
    [switch]$NoVerify
)

$ErrorActionPreference = 'Stop'

# Resolve project root: scripts/ -> setup-environment/ -> skills/ -> .github/ -> ROOT
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir '..\..\..\..')
Set-Location $ProjectRoot

function Write-Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok  ([string]$msg) { Write-Host "[OK] $msg"   -ForegroundColor Green }
function Write-Warn2([string]$msg){ Write-Host "[!]  $msg"   -ForegroundColor Yellow }
function Write-Err ([string]$msg) { Write-Host "[X]  $msg"   -ForegroundColor Red }

function Show-ModelConfigHelp {
  Write-Host "`nModel configuration is required before setup can continue." -ForegroundColor Yellow
  Write-Host "You can use one of these providers:" -ForegroundColor Yellow
  Write-Host "  - Qwen:     get a key from Alibaba Cloud Bailian / DashScope (https://bailian.console.aliyun.com/), fill MODEL_PROVIDER=qwen, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL."
  Write-Host "  - DeepSeek: get a key from DeepSeek Platform (https://platform.deepseek.com/api_keys), fill MODEL_PROVIDER=deepseek, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL. Use Qwen/Zhipu/OpenAI for embeddings."
  Write-Host "  - Zhipu:    get a key from BigModel (https://bigmodel.cn/usercenter/proj-mgmt/apikeys), fill MODEL_PROVIDER=zhipu, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL."
  Write-Host "  - OpenAI:   get a key from OpenAI Platform (https://platform.openai.com/api-keys), fill MODEL_PROVIDER=openai, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL."
  Write-Host "  - Azure:    get a key from Azure Portal (https://portal.azure.com/), fill MODEL_PROVIDER=azure and the AZURE_OPENAI_* values."
  Write-Host "`nFill these values in .env, then tell me you are ready and I will continue setup." -ForegroundColor Yellow
}

function Get-EnvValues([string]$content) {
  $values = @{}
  foreach ($rawLine in $content -split "`n") {
    $line = $rawLine.Trim()
    if (-not $line -or $line.StartsWith('#') -or -not $line.Contains('=')) { continue }
    $key, $value = $line.Split('=', 2)
    $values[$key.Trim()] = $value.Trim().Trim('"').Trim("'")
  }
  return $values
}

function Get-IncompleteModelKeys([string]$content) {
  $values = Get-EnvValues $content
  $provider = 'azure'
  if ($values.ContainsKey('MODEL_PROVIDER') -and $values['MODEL_PROVIDER']) {
    $provider = $values['MODEL_PROVIDER']
  }
  $provider = $provider.ToLowerInvariant()

  $embeddingProvider = $provider
  if ($values.ContainsKey('EMBEDDING_PROVIDER') -and $values['EMBEDDING_PROVIDER']) {
    $embeddingProvider = $values['EMBEDDING_PROVIDER']
  }
  $embeddingProvider = $embeddingProvider.ToLowerInvariant()
  $required = @('MODEL_PROVIDER')

  if ($provider -eq 'azure') {
    $required += @('AZURE_OPENAI_API_KEY', 'AZURE_OPENAI_ENDPOINT', 'AZURE_OPENAI_DEPLOYMENT', 'AZURE_OPENAI_VERSION')
  } else {
    $required += @('LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL')
  }

  $required += 'EMBEDDING_PROVIDER'
  if ($embeddingProvider -eq 'azure') {
    $required += @('AZURE_OPENAI_API_KEY', 'AZURE_OPENAI_ENDPOINT_EMBEDDING', 'AZURE_OPENAI_DEPLOYMENT_EMBEDDING')
  } else {
    $required += @('EMBEDDING_API_KEY', 'EMBEDDING_BASE_URL', 'EMBEDDING_MODEL')
  }

  $incomplete = @()
  foreach ($key in $required) {
    if (-not $values.ContainsKey($key) -or -not $values[$key] -or $values[$key] -match 'your_[a-zA-Z0-9_]*_here') {
      $incomplete += $key
    }
  }
  return $incomplete | Select-Object -Unique
}

# ---------------------------------------------------------------- 1. Python
# Required range: 3.10 <= Python < 3.13.
# Python 3.13/3.14 break LangChain 0.3.x via PEP 649 deferred annotation evaluation
# (TypeError: 'function' object is not subscriptable on pydantic forward refs).
Write-Step "Checking Python"

function Get-PythonExe {
    $candidates = @()
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        foreach ($v in '3.12','3.11','3.10') {
            try {
                $exe = & cmd /c "py -$v -c `"import sys; print(sys.executable)`" 2>nul"
                if ($LASTEXITCODE -eq 0 -and $exe) {
                    $exe = ($exe -split "`n" | Where-Object { $_.Trim() } | Select-Object -First 1).Trim()
                    if ($exe) { $candidates += $exe }
                }
            } catch { }
        }
    }
    $defaultPy = Get-Command python -ErrorAction SilentlyContinue
    if ($defaultPy) { $candidates += $defaultPy.Source }

    foreach ($exe in $candidates) {
        if (-not (Test-Path $exe)) { continue }
        $ver = & $exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if (-not $ver) { continue }
        $parts = $ver.Split('.')
        $maj = [int]$parts[0]; $min = [int]$parts[1]
        if ($maj -eq 3 -and $min -ge 10 -and $min -le 12) {
            return [pscustomobject]@{ Exe = $exe; Version = $ver }
        }
    }
    return $null
}

$pyInfo = Get-PythonExe
if (-not $pyInfo) {
    Write-Err "No compatible Python found. Need Python 3.10, 3.11, or 3.12 (3.13/3.14 break LangChain 0.3.x). Install from https://www.python.org/downloads/"
    exit 1
}
$PythonExe = $pyInfo.Exe
Write-Ok "Python $($pyInfo.Version) at $PythonExe"

# ---------------------------------------------------------------- 2. .env gate
Write-Step "Checking model configuration"
$EnvFile     = Join-Path $ProjectRoot '.env'
$EnvExample  = Join-Path $ProjectRoot '.env.example'

if (-not (Test-Path $EnvExample)) {
  @"
MODEL_PROVIDER=qwen
LLM_API_KEY=your_llm_api_key_here
LLM_BASE_URL=your_openai_compatible_chat_base_url_here
LLM_MODEL=your_chat_model_name_here
EMBEDDING_PROVIDER=qwen
EMBEDDING_API_KEY=your_embedding_api_key_here
EMBEDDING_BASE_URL=your_openai_compatible_embedding_base_url_here
EMBEDDING_MODEL=your_embedding_model_name_here
OPENWEATHER_API_KEY=your_openweather_api_key_here
"@ | Set-Content -Path $EnvExample -Encoding UTF8
  Write-Ok ".env.example created"
}

if (-not (Test-Path $EnvFile)) {
  Copy-Item $EnvExample $EnvFile
  Write-Warn2 ".env was missing. A template was copied from .env.example."
}

$envContent = Get-Content $EnvFile -Raw
$incompleteKeys = @(Get-IncompleteModelKeys $envContent)
if ($incompleteKeys.Count -gt 0) {
  Show-ModelConfigHelp
  Write-Host "`nMissing or placeholder values: $($incompleteKeys -join ', ')" -ForegroundColor Yellow
  exit 2
}
Write-Ok ".env model configuration looks filled"

# ---------------------------------------------------------------- 3. .venv
Write-Step "Preparing virtual environment (.venv)"
if ($Force -and (Test-Path .venv)) {
    Write-Warn2 "Removing existing .venv (forced)"
    Remove-Item -Recurse -Force .venv
}
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

# Detect mismatched venv (created with different Python version) and rebuild.
if ((Test-Path .venv) -and (Test-Path $VenvPython)) {
    $venvVer = & $VenvPython -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($venvVer -and $venvVer -ne $pyInfo.Version) {
        Write-Warn2 ".venv was built with Python $venvVer, but selected Python is $($pyInfo.Version). Rebuilding."
        Remove-Item -Recurse -Force .venv
    }
}

if (-not (Test-Path .venv)) {
    & $PythonExe -m venv .venv
    Write-Ok ".venv created (Python $($pyInfo.Version))"
} else {
    Write-Ok ".venv already exists"
}

if (-not (Test-Path $VenvPython)) { Write-Err ".venv python missing"; exit 1 }

# ---------------------------------------------------------------- 4. pip install
Write-Step "Installing dependencies (this may take a minute)"
& $VenvPython -m pip install --upgrade pip --quiet
& $VenvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Err "pip install failed"; exit 1 }
Write-Ok "Dependencies installed"

# ---------------------------------------------------------------- 5. data dir
Write-Step "Ensuring data/ directory"
New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot 'data') | Out-Null
Write-Ok "data/ ready"

# ---------------------------------------------------------------- 6. verify
if (-not $NoVerify) {
    Write-Step "Verifying installation"
    & $VenvPython (Join-Path $ScriptDir 'verify_env.py')
    if ($LASTEXITCODE -ne 0) { Write-Err "verify_env.py failed"; exit 1 }
}

Write-Host "`n========================================================" -ForegroundColor Green
Write-Host " Setup complete." -ForegroundColor Green
Write-Host " Activate the venv with:  .\.venv\Scripts\Activate.ps1"   -ForegroundColor Green
Write-Host " Then launch the app:     uvicorn app:app --host 127.0.0.1 --port 8001 --reload" -ForegroundColor Green
Write-Host "========================================================`n" -ForegroundColor Green

# ---------------------------------------------------------------- 7. optional run
if ($Run) {
    Write-Step "Launching uvicorn on 127.0.0.1:8001"
    & $VenvPython -m uvicorn app:app --host 127.0.0.1 --port 8001 --reload
}
