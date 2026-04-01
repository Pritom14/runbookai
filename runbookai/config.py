from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    token0_base_url: str = "http://localhost:8000"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "qwen2.5:7b"
    pagerduty_webhook_secret: str = ""
    database_url: str = "sqlite+aiosqlite:///./runbookai.db"
    suggest_mode: bool = True  # False = autonomous execution
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    escalation_email: str = ""  # where to send escalation alerts

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
