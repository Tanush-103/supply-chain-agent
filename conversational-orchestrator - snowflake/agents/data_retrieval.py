from typing import Dict, Any, Tuple
import pandas as pd
import numpy as np


from tools.connectors import FileConnector, SQLConnector, SnowflakeConnector
from tools.semantics import SemanticMatcher




INVENTORY_SQL = """
SELECT sku, description, stock_on_hand, unit_volume, supplier
FROM {db}.{schema}.{inventory_table}
"""



DEMAND_SQL = """
SELECT sku, period, demand
FROM {db}.{schema}.{demand_table}
"""



SUPPLIERS_SQL = """
SELECT supplier_id as supplier, lead_time_days, moq
FROM {db}.{schema}.{suppliers_table}
"""

TRANSPORT_SQL = """
SELECT sku, per_unit_transport_cost
FROM {db}.{schema}.{transport_table}
"""


class DataRetrievalAgent:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        doc_root = cfg["retrieval"]["doc_root"]
        self.files = FileConnector(doc_root)
        self.sql = None
        if cfg.get("sql", {}).get("enabled"):
            self.sql = SQLConnector(cfg["sql"]["connection_string"])
        self.matcher = SemanticMatcher()
        self.fast_thresh = float(cfg["retrieval"].get("fast_moving_threshold", 0.8))

        if cfg.get("snowflake", {}).get("enabled"):
            self.sql = SnowflakeConnector(cfg["snowflake"])
        elif cfg.get("sql", {}).get("enabled"):
            self.sql = SQLConnector(cfg["sql"]["connection_string"])

    def _load_frames_csv(self) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        inv = self.files.read_csv("inventory.csv")
        dem = self.files.read_csv("demand_forecast.csv")
        sup = self.files.read_csv("suppliers.csv")
        tc  = self.files.read_csv("transport_costs.csv")
        return inv, dem, sup, tc

    def _load_frames_snowflake(self) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        src    = self.cfg.get("sources", {})
        snow   = self.cfg["snowflake"]
        db     = snow["database"]
        schema = snow["schema"]

        inv = self.sql.query(INVENTORY_SQL.format(db=db, schema=schema, inventory_table=src.get("inventory_table", "inventory")))
        dem = self.sql.query(DEMAND_SQL.format(db=db, schema=schema, demand_table=src.get("demand_table", "demand")))
        sup = self.sql.query(SUPPLIERS_SQL.format(db=db, schema=schema, suppliers_table=src.get("suppliers_table", "supplier")))
        tc  = self.sql.query(TRANSPORT_SQL.format(db=db, schema=schema, transport_table=src.get("transport_table", "transportation")))
        return inv, dem, sup, tc

    def _load_frames(self) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        if self.sql is not None:
            return self._load_frames_snowflake()
        return self._load_frames_csv()



    def retrieve(self, query: str, top_k: int = 100) -> Dict[str, pd.DataFrame]:
        topics = self.matcher.match(query)
        inv, dem, sup, tc = self._load_frames()


        # Compute demand velocity for fast-moving selection
        dem_pivot = dem.pivot_table(index="sku", values="demand", aggfunc=[np.mean, np.std])
        dem_pivot.columns = ["demand_mean", "demand_std"]
        dem_pivot = dem_pivot.reset_index()
        merged = inv.merge(dem_pivot, on="sku", how="left").merge(sup, on="supplier", how="left").merge(tc, on="sku", how="left")
        merged["demand_mean"].fillna(0.0, inplace=True)


        out = {
        "inventory": inv,
        "forecast": dem,
        "suppliers": sup,
        "transport_costs": tc,
        "merged": merged,
        }


        if "fast_moving" in topics or "reorder" in topics:
            q = merged.copy()
            # velocity proxy = mean demand
            cutoff = q["demand_mean"].quantile(self.fast_thresh)
            fast = q[q["demand_mean"] >= cutoff].sort_values("demand_mean", ascending=False).head(top_k)
            out["fast_moving"] = fast
        return out