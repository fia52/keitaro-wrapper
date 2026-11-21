from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Загружает локальную конфигурацию и секреты из окружения."""

    model_config: SettingsConfigDict = SettingsConfigDict(
        env_file='.env', extra='ignore'
    )
    keitaro_base_url: str = 'https://tlgk.host'
    keitaro_api_key: SecretStr = SecretStr('')
    keitaro_domain: str = 'kylovoa.com'
    keitaro_group: str = 'FORTESTS'
    database_url: str = 'sqlite:///./keitaro.db'
