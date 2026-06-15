#!/usr/bin/env python3
"""
Smoke test for the Smart Appointment AI Agent environment.

Imports every critical third-party package declared in requirements.txt and
checks the .env file for required model provider keys. Exits with a non-zero
status if anything is missing.

Usage:
    python .github/skills/setup-environment/scripts/verify_env.py
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

# (module name, friendly label)
PACKAGES: list[tuple[str, str]] = [
    ("fastapi",                "fastapi"),
    ("uvicorn",                "uvicorn"),
    ("jinja2",                 "jinja2"),
    ("multipart",              "python-multipart"),
    ("pydantic",               "pydantic"),
    ("aiofiles",               "aiofiles"),
    ("dotenv",                 "python-dotenv"),
    ("langchain",              "langchain"),
    ("langchain_openai",       "langchain-openai"),
    ("langchain_core",         "langchain-core"),
    ("langchain_community",    "langchain-community"),
    ("faiss",                  "faiss-cpu"),
    ("numpy",                  "numpy"),
    ("schedule",               "schedule"),
    ("sqlalchemy",             "sqlalchemy"),
    ("requests",               "requests"),
    ("mcp",                    "mcp"),
    ("langchain_experimental", "langchain-experimental"),
]

CHAT_PROVIDERS = {"qwen", "deepseek", "zhipu", "openai", "openai-compatible"}
EMBEDDING_PROVIDERS = {"qwen", "zhipu", "openai", "openai-compatible"}
PLACEHOLDER_HINTS = ("your_",)

GREEN, RED, YELLOW, OFF = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def check_imports() -> list[str]:
    failures: list[str] = []
    for module, label in PACKAGES:
        try:
            importlib.import_module(module)
            print(f"  {GREEN}OK{OFF}   {label}")
        except Exception as exc:  # pragma: no cover
            print(f"  {RED}FAIL{OFF} {label}  ({exc.__class__.__name__}: {exc})")
            failures.append(label)
    return failures


def check_env(project_root: Path) -> tuple[list[str], list[str]]:
    env_file = project_root / ".env"
    if not env_file.exists():
        return [".env"], []

    values: dict[str, str] = {}
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip().strip('"').strip("'")

    provider = values.get("MODEL_PROVIDER", "azure").strip().lower()
    embedding_provider = values.get("EMBEDDING_PROVIDER", provider).strip().lower()

    required: list[str] = ["MODEL_PROVIDER"]
    if provider == "azure":
        required.extend([
            "AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_ENDPOINT",
            "AZURE_OPENAI_DEPLOYMENT",
            "AZURE_OPENAI_VERSION",
        ])
    elif provider in CHAT_PROVIDERS:
        required.extend(["LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"])
    else:
        required.append(f"unsupported MODEL_PROVIDER={provider}")

    required.append("EMBEDDING_PROVIDER")
    if embedding_provider == "azure":
        required.extend([
            "AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_ENDPOINT_EMBEDDING",
            "AZURE_OPENAI_DEPLOYMENT_EMBEDDING",
        ])
    elif embedding_provider in EMBEDDING_PROVIDERS:
        required.extend(["EMBEDDING_API_KEY", "EMBEDDING_BASE_URL", "EMBEDDING_MODEL"])
    else:
        required.append(f"unsupported EMBEDDING_PROVIDER={embedding_provider}")

    missing = [k for k in required if "=" not in k and not values.get(k)]
    missing.extend([k for k in required if "=" in k])
    placeholder = [
        k for k in required
        if "=" not in k and values.get(k) and any(p in values[k] for p in PLACEHOLDER_HINTS)
    ]
    return missing, placeholder


def main() -> int:
    # scripts/ -> setup-environment/ -> skills/ -> .github/ -> ROOT
    project_root = Path(__file__).resolve().parents[4]

    print(f"\nPython: {sys.version.split()[0]}  ({sys.executable})")
    print(f"Project: {project_root}\n")

    py_major, py_minor = sys.version_info[:2]
    if not (py_major == 3 and 10 <= py_minor <= 12):
        print(
            f"  {RED}FAIL{OFF} Python {py_major}.{py_minor} is not supported. "
            f"Use Python 3.10, 3.11, or 3.12 (3.13/3.14 break LangChain 0.3.x)."
        )
        return 1

    print("Imports:")
    failures = check_imports()

    print("\n.env:")
    missing, placeholder = check_env(project_root)
    if missing:
        for k in missing:
            print(f"  {RED}MISSING{OFF} {k}")
    if placeholder:
        for k in placeholder:
            print(f"  {YELLOW}PLACEHOLDER{OFF} {k}")
    if not missing and not placeholder:
        print(f"  {GREEN}OK{OFF}   all required keys present")

    print()
    if failures:
        print(f"{RED}Verification failed.{OFF} Missing packages: {', '.join(failures)}")
        return 1
    if missing:
        print(f"{RED}Verification failed.{OFF} .env is missing required keys.")
        return 1
    if placeholder:
        print(f"{YELLOW}Verification passed with warnings.{OFF} "
              f"Fill placeholder keys in .env before launching the app.")
        return 0
    print(f"{GREEN}Verification passed.{OFF} Environment is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
