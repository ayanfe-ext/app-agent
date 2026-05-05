from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    groq_console_url: str = ""
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    duplo_checkout_url: str = ""
    duplo_base_url: str = ""
    duplo_api_key: str = ""

    conversation_db_path: str = "conversations.sqlite3"
    app_api_key: str = ""
    rate_limit_per_minute: int = 0

    arize_enabled: bool = False
    arize_space_id: str = ""
    arize_api_key: str = ""
    arize_project_name: str = "fastapi-payment-agent"
    arize_log_to_console: bool = False

    host: str = "0.0.0.0"
    port: int = 8000

    class Config:
        env_file = ".env"


settings = Settings()
