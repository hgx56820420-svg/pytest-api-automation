"""LLM client bootstrap for the requirement analyst agent.

配置来源：仓库根目录 ``.env``（已 gitignore）或进程环境变量。

    LLM_API_KEY   必填才启用 LLM
    LLM_BASE_URL  OpenAI 兼容网关地址
    LLM_MODEL     模型名

安全约定：Key 只存环境变量/.env，不进 git；LLM 的全部输出必须经过
Pydantic 校验并由确定性引擎执行，模型本身永远不产生 SQL 或代码。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def load_env_file() -> dict[str, str]:
    """Parse the repo-root .env once; real environment variables win."""
    values: dict[str, str] = {}
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def llm_setting(name: str) -> str:
    return os.environ.get(name) or load_env_file().get(name, "")


def is_llm_enabled() -> bool:
    """LLM analysis requires the triple config; the on/off switch is the
    caller's explicit flag (CLI --llm-analysis), not a hidden env var."""
    return bool(llm_setting("LLM_API_KEY") and llm_setting("LLM_BASE_URL") and llm_setting("LLM_MODEL"))


def get_chat_model():
    """Build a LangChain ChatOpenAI bound to the configured gateway."""
    import re

    from langchain_openai import ChatOpenAI

    base = llm_setting("LLM_BASE_URL").rstrip("/")
    # 已带版本段的网关（智谱 /v4、千帆 /v2 等）原样使用，否则补 OpenAI 默认 /v1
    if not re.search(r"/v\d+$", base):
        base += "/v1"
    return ChatOpenAI(
        api_key=llm_setting("LLM_API_KEY"),
        base_url=base,
        model=llm_setting("LLM_MODEL"),
        temperature=0,
        timeout=180,
        max_retries=6,  # 免费档高峰期常见 429，依赖 SDK 指数退避
    )
