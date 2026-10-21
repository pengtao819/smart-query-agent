"""应用配置：从环境变量 / .env 读取，集中管理，避免代码里散落硬编码。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env_file 让本地开发只需一个 .env；extra="ignore" 允许 .env 里有额外变量
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM：默认走阿里云百炼的 OpenAI 兼容接口，换模型只改环境变量
    llm_api_key: str = ""
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus"

    # 数据库文件路径（相对项目根目录）
    duckdb_path: str = "data/analytics.duckdb"

    # 应用
    app_name: str = "智能问数 Agent"
    debug: bool = False


@lru_cache
def get_settings() -> Settings:
    """缓存单例：整个进程只解析一次配置。"""
    return Settings()