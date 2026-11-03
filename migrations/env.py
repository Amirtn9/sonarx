from logging.config import fileConfig
import os
from dotenv import load_dotenv
from alembic import context
from sqlalchemy import engine_from_config, pool
load_dotenv()
config=context.config
if config.config_file_name: fileConfig(config.config_file_name)
url=os.getenv('DATABASE_URL','')
if url: config.set_main_option('sqlalchemy.url',url.replace('%','%%'))
def run_migrations_offline():
    context.configure(url=config.get_main_option('sqlalchemy.url'),literal_binds=True,dialect_opts={'paramstyle':'named'})
    with context.begin_transaction(): context.run_migrations()
def run_migrations_online():
    connectable=engine_from_config(config.get_section(config.config_ini_section),prefix='sqlalchemy.',poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection,compare_type=True)
        with context.begin_transaction(): context.run_migrations()
if context.is_offline_mode(): run_migrations_offline()
else: run_migrations_online()
