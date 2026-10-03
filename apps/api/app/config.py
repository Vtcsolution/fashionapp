from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]  # project root (holds .env and storage/)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore", case_sensitive=False)

    cors_origins: str = "http://localhost:3000"

    # Fresh prototype uses its own SQLite DB; the shared DATABASE_URL in .env is ignored on purpose.
    vto_database_url: str = f"sqlite:///{(ROOT / 'storage' / 'vto.db').as_posix()}"
    storage_dir: Path = ROOT / "storage"

    # Retailers
    ebay_client_id: str = ""
    ebay_client_secret: str = ""
    ali_express_app_key: str = ""
    ali_express_secret_api: str = ""
    ali_express_tracking_id: str = ""
    cj_api_token: str = ""
    cj_company_id: str = ""  # publisher CID (not the website/PID); required by the CJ GraphQL API
    rakuten_enabled: bool = False
    rakuten_client_id: str = ""
    rakuten_client_secret: str = ""
    rakuten_account_id: str = ""

    # FASHN
    fashn_api_key: str = ""
    fashn_model: str = "tryon-max"
    fashn_live_enabled: bool = False  # must be flipped explicitly; default OFF
    fashn_live_authorization: str = ""  # must equal the exact phrase to allow a live call
    fashn_credit_cap: int = 2

    # Image rules
    min_product_image_side: int = 512

    def configured(self) -> dict[str, bool]:
        return {
            "ebay": bool(self.ebay_client_id and self.ebay_client_secret),
            "aliexpress": bool(self.ali_express_app_key and self.ali_express_secret_api),
            "cj": bool(self.cj_api_token and self.cj_company_id),
            "rakuten": bool(self.rakuten_enabled and self.rakuten_client_id and self.rakuten_client_secret),
            "fashn_key": bool(self.fashn_api_key),
        }


settings = Settings()
