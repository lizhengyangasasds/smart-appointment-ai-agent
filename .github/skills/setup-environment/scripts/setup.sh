#!/usr/bin/env bash
# One-shot environment bootstrapper for the Smart Appointment AI Agent (macOS / Linux).
#
# Flags:
#   --force        recreate .venv from scratch
#   --run          after setup, launch uvicorn on 127.0.0.1:8001
#   --no-verify    skip verify_env.py
set -euo pipefail

FORCE=0
RUN=0
VERIFY=1
for arg in "$@"; do
    case "$arg" in
        --force)     FORCE=1 ;;
        --run)       RUN=1 ;;
        --no-verify) VERIFY=0 ;;
        *) echo "Unknown flag: $arg" >&2; exit 2 ;;
    esac
done

SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$( cd -- "$SCRIPT_DIR/../../../.." &> /dev/null && pwd )"
cd "$PROJECT_ROOT"

C_CYAN='\033[36m'; C_GREEN='\033[32m'; C_YELLOW='\033[33m'; C_RED='\033[31m'; C_OFF='\033[0m'
step() { printf "\n${C_CYAN}==> %s${C_OFF}\n" "$1"; }
ok()   { printf "${C_GREEN}[OK] %s${C_OFF}\n" "$1"; }
warn() { printf "${C_YELLOW}[!]  %s${C_OFF}\n" "$1"; }
err()  { printf "${C_RED}[X]  %s${C_OFF}\n" "$1" >&2; }

show_model_config_help() {
    printf "\n${C_YELLOW}Model configuration is required before setup can continue.${C_OFF}\n"
    printf "${C_YELLOW}You can use one of these providers:${C_OFF}\n"
    printf "  - Qwen:     get a key from Alibaba Cloud Bailian / DashScope (https://bailian.console.aliyun.com/), fill MODEL_PROVIDER=qwen, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL.\n"
    printf "  - DeepSeek: get a key from DeepSeek Platform (https://platform.deepseek.com/api_keys), fill MODEL_PROVIDER=deepseek, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL. Use Qwen/Zhipu/OpenAI for embeddings.\n"
    printf "  - Zhipu:    get a key from BigModel (https://bigmodel.cn/usercenter/proj-mgmt/apikeys), fill MODEL_PROVIDER=zhipu, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL.\n"
    printf "  - OpenAI:   get a key from OpenAI Platform (https://platform.openai.com/api-keys), fill MODEL_PROVIDER=openai, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL.\n"
    printf "  - Azure:    get a key from Azure Portal (https://portal.azure.com/), fill MODEL_PROVIDER=azure and the AZURE_OPENAI_* values.\n"
    printf "\n${C_YELLOW}Fill these values in .env, then tell me you are ready and I will continue setup.${C_OFF}\n"
}

get_env_value() {
    local key="$1"
    if [ ! -f "$ENV_FILE" ]; then
        return 0
    fi
    grep -E "^${key}=" "$ENV_FILE" | tail -n 1 | cut -d '=' -f 2- | sed "s/^['\"]//;s/['\"]$//"
}

is_incomplete_value() {
    local value="$1"
    [ -z "$value" ] || printf '%s' "$value" | grep -q 'your_[A-Za-z0-9_]*_here'
}

collect_incomplete_model_keys() {
    local provider embedding_provider required key value incomplete
    provider="$(get_env_value MODEL_PROVIDER)"
    [ -n "$provider" ] || provider="azure"
    provider="$(printf '%s' "$provider" | tr '[:upper:]' '[:lower:]')"
    embedding_provider="$(get_env_value EMBEDDING_PROVIDER)"
    [ -n "$embedding_provider" ] || embedding_provider="$provider"
    embedding_provider="$(printf '%s' "$embedding_provider" | tr '[:upper:]' '[:lower:]')"

    required="MODEL_PROVIDER"
    if [ "$provider" = "azure" ]; then
        required="$required AZURE_OPENAI_API_KEY AZURE_OPENAI_ENDPOINT AZURE_OPENAI_DEPLOYMENT AZURE_OPENAI_VERSION"
    else
        required="$required LLM_API_KEY LLM_BASE_URL LLM_MODEL"
    fi

    required="$required EMBEDDING_PROVIDER"
    if [ "$embedding_provider" = "azure" ]; then
        required="$required AZURE_OPENAI_API_KEY AZURE_OPENAI_ENDPOINT_EMBEDDING AZURE_OPENAI_DEPLOYMENT_EMBEDDING"
    else
        required="$required EMBEDDING_API_KEY EMBEDDING_BASE_URL EMBEDDING_MODEL"
    fi

    incomplete=""
    for key in $required; do
        value="$(get_env_value "$key")"
        if is_incomplete_value "$value"; then
            incomplete="$incomplete $key"
        fi
    done
    printf '%s' "$incomplete" | xargs
}

# ----------------------------------------------------------- 1. Python
# Required range: 3.10 <= Python < 3.13.
# Python 3.13/3.14 break LangChain 0.3.x via PEP 649 deferred annotation evaluation
# (TypeError: 'function' object is not subscriptable on pydantic forward refs).
step "Checking Python"

find_python() {
    local cand ver maj min
    for cand in python3.12 python3.11 python3.10 python3; do
        if command -v "$cand" >/dev/null 2>&1; then
            ver="$("$cand" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
            maj="${ver%%.*}"; min="${ver##*.}"
            if [ "$maj" = "3" ] && [ "$min" -ge 10 ] && [ "$min" -le 12 ]; then
                echo "$cand|$ver"
                return 0
            fi
        fi
    done
    return 1
}
PY_PICK="$(find_python || true)"
if [ -z "$PY_PICK" ]; then
    err "No compatible Python found. Need Python 3.10, 3.11, or 3.12 (3.13/3.14 break LangChain 0.3.x)."
    exit 1
fi
PYTHON_BIN="${PY_PICK%%|*}"
PY_VER="${PY_PICK##*|}"
ok "Python $PY_VER ($PYTHON_BIN)"

# ----------------------------------------------------------- 2. .env gate
step "Checking model configuration"
ENV_FILE="$PROJECT_ROOT/.env"
ENV_EXAMPLE="$PROJECT_ROOT/.env.example"
if [ ! -f "$ENV_EXAMPLE" ]; then
    cat > "$ENV_EXAMPLE" <<'EOF'
MODEL_PROVIDER=qwen
LLM_API_KEY=your_llm_api_key_here
LLM_BASE_URL=your_openai_compatible_chat_base_url_here
LLM_MODEL=your_chat_model_name_here
EMBEDDING_PROVIDER=qwen
EMBEDDING_API_KEY=your_embedding_api_key_here
EMBEDDING_BASE_URL=your_openai_compatible_embedding_base_url_here
EMBEDDING_MODEL=your_embedding_model_name_here
OPENWEATHER_API_KEY=your_openweather_api_key_here
EOF
    ok ".env.example created"
fi
if [ ! -f "$ENV_FILE" ]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    warn ".env was missing. A template was copied from .env.example."
fi
INCOMPLETE_KEYS="$(collect_incomplete_model_keys)"
if [ -n "$INCOMPLETE_KEYS" ]; then
    show_model_config_help
    warn "Missing or placeholder values: $INCOMPLETE_KEYS"
    exit 2
fi
ok ".env model configuration looks filled"

# ----------------------------------------------------------- 3. .venv
step "Preparing virtual environment (.venv)"
if [ "$FORCE" -eq 1 ] && [ -d .venv ]; then
    warn "Removing existing .venv (forced)"; rm -rf .venv
fi
if [ -d .venv ] && [ -x .venv/bin/python ]; then
    EXISTING_VER="$(.venv/bin/python -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
    if [ -n "$EXISTING_VER" ] && [ "$EXISTING_VER" != "$PY_VER" ]; then
        warn ".venv was built with Python $EXISTING_VER, but selected Python is $PY_VER. Rebuilding."
        rm -rf .venv
    fi
fi
if [ ! -d .venv ]; then
    "$PYTHON_BIN" -m venv .venv
    ok ".venv created (Python $PY_VER)"
else
    ok ".venv already exists"
fi
VENV_PY="$PROJECT_ROOT/.venv/bin/python"

# ----------------------------------------------------------- 4. pip install
step "Installing dependencies (this may take a minute)"
"$VENV_PY" -m pip install --upgrade pip --quiet
"$VENV_PY" -m pip install -r requirements.txt
ok "Dependencies installed"

# ----------------------------------------------------------- 5. data dir
step "Ensuring data/ directory"
mkdir -p "$PROJECT_ROOT/data"
ok "data/ ready"

# ----------------------------------------------------------- 6. verify
if [ "$VERIFY" -eq 1 ]; then
    step "Verifying installation"
    "$VENV_PY" "$SCRIPT_DIR/verify_env.py"
fi

cat <<EOF

${C_GREEN}========================================================
 Setup complete.
 Activate the venv with:  source .venv/bin/activate
 Then launch the app:     uvicorn app:app --host 127.0.0.1 --port 8001 --reload
========================================================${C_OFF}

EOF

# ----------------------------------------------------------- 7. optional run
if [ "$RUN" -eq 1 ]; then
    step "Launching uvicorn on 127.0.0.1:8001"
    exec "$VENV_PY" -m uvicorn app:app --host 127.0.0.1 --port 8001 --reload
fi
