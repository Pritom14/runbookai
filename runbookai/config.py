from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    token0_base_url: str = "http://localhost:8000"
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "qwen2.5:7b"
    llm_api_key: str = "ollama"  # "ollama" for Ollama; real key for OpenAI/Anthropic/Groq
    pagerduty_webhook_secret: str = ""
    pagerduty_api_key: str = ""  # API key for PagerDuty write-back (resolve incidents)
    database_url: str = "sqlite+aiosqlite:///./runbookai.db"
    suggest_mode: bool = True  # False = autonomous execution
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    escalation_email: str = ""  # where to send escalation alerts
    slack_webhook_url: str = ""
    # SSH defaults — used when no HostCredential row exists for a given host.
    ssh_default_username: str = ""
    ssh_private_key_path: str = ""  # path to PEM file, e.g. ~/.ssh/id_rsa
    # Demo mode — returns pre-canned responses; no real SSH connections made.
    demo_mode: bool = False
    # Hardware mode: "emulated" (in-memory sensors) or "real" (IPMI BMC calls)
    hardware_mode: str = "emulated"
    # BMC configuration for real hardware mode
    bmc_ip: str = ""  # IP address of BMC, e.g. 10.0.0.50
    bmc_username: str = ""  # BMC username
    bmc_password: str = ""  # BMC password

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
