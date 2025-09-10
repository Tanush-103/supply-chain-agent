from typing import Optional
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