from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ZM Terminal API"
    database_url: str = "postgresql+psycopg://zm_terminal:zm_terminal_dev@postgres:5432/zm_terminal"
    openbb_url: str = "http://openbb:6900"
    boz_usd_rates_url: str = "https://www.boz.zm/api/v1/views/boz_zmw_usd_daily_exchange_rates"
    boz_treasury_bills_url: str = "https://www.boz.zm/jsonapi/node/tbills_auction_results"
    boz_bond_auctions_url: str = "https://www.boz.zm/jsonapi/node/bond_auction"
    erb_media_url: str = "https://www.erb.org.zm/wp-json/wp/v2/media"
    moa_media_url: str = "https://www.agriculture.gov.zm/wp-json/wp/v2/media"
    moa_ca_bundle: str = ""
    worldbank_commodity_page_url: str = "https://www.worldbank.org/en/research/commodity-markets"
    cors_origins: str = "http://localhost:5173"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
