from typing import Optional, Dict, Any
import os
import pandas as pd
from sqlalchemy import create_engine, text


class FileConnector:
    def __init__(self, root: str):
        self.root = root


    def read_csv(self, name: str) -> pd.DataFrame:
        path = os.path.join(self.root, name)
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        return pd.read_csv(path)


class SQLConnector:
    def __init__(self, conn_str: str):
        self.engine = create_engine(conn_str, future=True)


    def query(self, sql: str) -> pd.DataFrame:
        with self.engine.connect() as conn:
            return pd.read_sql(text(sql), conn)
        

class SnowflakeConnector:
    """SQLAlchemy engine for Snowflake. Supports password, key-pair, or SSO."""
    def __init__(self, cfg: Dict[str, Any]):
        from urllib.parse import quote_plus
        acct   = cfg.get("account")
        user   = cfg.get("user")
        role   = cfg.get("role")
        wh     = cfg.get("warehouse")
        db     = cfg.get("database")
        schema = cfg.get("schema")
        password = cfg.get("password")

        # Prefer env for secrets
        #password = os.getenv(cfg.get("password_env", ""), None)

        # Base URL; add authenticator if using SSO:
        #   &authenticator=externalbrowser
        base = f"snowflake://{user}@{acct}/{db}/{schema}?role={role}&warehouse={wh}"
        if password:
            base = f"snowflake://{user}:{quote_plus(password)}@{acct}/{db}/{schema}?role={role}&warehouse={wh}"
        # If using externalbrowser auth, append: &authenticator=externalbrowser

        self.engine = create_engine(base)

    def query(self, sql: str) -> pd.DataFrame:
        with self.engine.connect() as conn:
            return pd.read_sql(sql, conn)