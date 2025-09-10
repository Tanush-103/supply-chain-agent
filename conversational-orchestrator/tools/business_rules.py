from typing import Dict, Any
import pandas as pd


class BusinessRules:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg or {}
        self.priority_skus = set((self.cfg.get("priority_skus") or []))
        self.priority_weight = float(self.cfg.get("priority_weight", 1.0))
        self.supplier_lead_time_days = self.cfg.get("supplier_lead_time_days", {})


    def apply_priority_weights(self, df: pd.DataFrame) -> pd.Series:
# Scale stockout penalty for priority SKUs
        mult = df["sku"].apply(lambda s: self.priority_weight if s in self.priority_skus else 1.0)
        return mult


    def get_supplier_lead(self, supplier: str, default: int = 14) -> int:
        return int(self.supplier_lead_time_days.get(supplier, default))