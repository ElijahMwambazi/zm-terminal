from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

DRIVERNAME = "postgresql+psycopg"


class Settings(BaseSettings):
    app_name: str = "ZM Terminal API"
    # Database: the discrete APP_DB_* settings are the single source of truth. They are the same values the Postgres container uses to
    # create the application role (db/init/015-016), so the role and the API cannot drift. There is deliberately no password default
    # here. DATABASE_URL is an optional override (it wins when set) and must never name the admin role.
    app_db_user: str = "zm_terminal_app"
    app_db_password: str = ""
    app_db_host: str = "postgres"
    app_db_port: int = 5432
    app_db_name: str = "zm_terminal"
    postgres_user: str = "zm_terminal"  # the admin/owner role NAME (not a credential); the runtime may never connect as it
    database_url: str = ""
    openbb_url: str = "http://openbb:6900"
    boz_usd_rates_url: str = "https://www.boz.zm/api/v1/views/boz_zmw_usd_daily_exchange_rates"
    boz_treasury_bills_url: str = "https://www.boz.zm/jsonapi/node/tbills_auction_results"
    boz_bond_auctions_url: str = "https://www.boz.zm/jsonapi/node/bond_auction"
    boz_commercial_bank_rates_url: str = "https://www.boz.zm/jsonapi/node/commercial_bank_daily_rates"
    erb_media_url: str = "https://www.erb.org.zm/wp-json/wp/v2/media"
    moa_media_url: str = "https://www.agriculture.gov.zm/wp-json/wp/v2/media"
    moa_ca_bundle: str = ""
    worldbank_commodity_page_url: str = "https://www.worldbank.org/en/research/commodity-markets"
    cors_origins: str = "http://localhost:5173"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def runtime_database_url(self) -> URL:
        """The SQLAlchemy URL the API uses. DATABASE_URL, when set, takes precedence over the APP_DB_* settings; either way the
        admin role is refused and no fallback to admin credentials exists. URL.create() handles password escaping."""
        if self.database_url.strip():
            try:
                url = make_url(self.database_url.strip())
            except Exception:
                raise ValueError("DATABASE_URL is not a valid database URL") from None  # never echo the value: it holds a password
        else:
            if not self.app_db_password:
                raise ValueError("APP_DB_PASSWORD is not set; the API needs the application role's password (or an explicit DATABASE_URL)")
            url = URL.create(DRIVERNAME, username=self.app_db_user, password=self.app_db_password, host=self.app_db_host,
                             port=self.app_db_port, database=self.app_db_name)
        if url.username == self.postgres_user:
            raise ValueError(f"the API must connect as the application role, not the admin role {self.postgres_user!r}; "
                             "unset DATABASE_URL or point it at the application role")
        return url

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
