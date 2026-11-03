from dataclasses import dataclass
import os
from dotenv import load_dotenv
load_dotenv()
@dataclass(frozen=True)
class Settings:
    bot_token: str=os.getenv('BOT_TOKEN','')
    admin_ids: tuple[int,...]=tuple(int(x.strip()) for x in os.getenv('ADMIN_IDS','').split(',') if x.strip().isdigit())
    database_path: str=os.getenv('DATABASE_PATH','data/sonarx.sqlite3')
    database_url: str=os.getenv('DATABASE_URL','').strip()
    timezone: str=os.getenv('TIMEZONE','Asia/Tehran')
    verify_tls: bool=os.getenv('PASARGUARD_VERIFY_TLS','true').lower() not in {'0','false','no'}
    fernet_key: str=os.getenv('FERNET_KEY','')
    currency_label: str=os.getenv('CURRENCY_LABEL','تومان')
    support_username: str=os.getenv('SUPPORT_USERNAME','')
    def validate(self):
        if not self.bot_token: raise RuntimeError('BOT_TOKEN is not set')
        if not self.admin_ids: raise RuntimeError('ADMIN_IDS must contain at least one numeric Telegram user ID')
        if self.database_url and not self.database_url.startswith(('postgresql://','postgres://','postgresql+psycopg://')):
            raise RuntimeError('DATABASE_URL must be a PostgreSQL URL')
settings=Settings()
