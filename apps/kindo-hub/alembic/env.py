from alembic import context
from sqlalchemy import engine_from_config, pool

from kindo.config import load_config
from kindo.models import Base

config = context.config
# 注意：不调用 logging fileConfig —— 它会重置 root logger，
# 破坏 kindo.logsetup 安装的 JSON 结构化日志（默认 disable_existing_loggers=True）。
import logging

logging.getLogger("alembic").setLevel(logging.INFO)

target_metadata = Base.metadata


def _db_url() -> str:
    injected = (config.attributes or {}).get("db_url")
    if injected:
        return str(injected)
    try:
        cfg = load_config()
        return f"sqlite:///{cfg.db_path}"
    except Exception:
        return config.get_main_option("sqlalchemy.url") or "sqlite:///kindo.db"


def run_migrations_offline() -> None:
    context.configure(url=_db_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _db_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
