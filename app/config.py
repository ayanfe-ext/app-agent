from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    llm_provider: str = "groq"
    llm_model: str = ""
    llm_base_url: str = ""

    groq_console_url: str = ""
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_base_url: str = ""

    duplo_checkout_url: str = ""
    duplo_base_url: str = ""
    duplo_api_key: str = ""
    duplo_payout_url: str = ""

    conversation_db_path: str = "conversations.sqlite3"
    app_api_key: str = ""
    merchant_api_key: str = ""
    rate_limit_per_minute: int = 0
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_exp_minutes: int = 60
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    atlas_webhook_verify: bool = False

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
