from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    token0_base_url: str = "http://localhost:8000"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "llama3.2:latest"
    pagerduty_webhook_secret: str = ""
    database_url: str = "sqlite+aiosqlite:///./runbookai.db"
    suggest_mode: bool = True  # False = autonomous execution

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
