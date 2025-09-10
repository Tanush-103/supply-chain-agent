from typing import Dict, Any, Tuple
import pandas as pd


class WhatIfAgent:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg


    def apply(self, merged: pd.DataFrame, demand_multiplier: float = 1.0, capacity_multiplier: float = 1.0, moq_overrides: Dict[str, float] = None) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        df = merged.copy()
        df["demand_mean"] = df["demand_mean"] * float(demand_multiplier)
        # modify capacity & MOQ in cfg for downstream optimization
        new_cfg = {**self.cfg}
        new_cfg["optimization"] = {**new_cfg.get("optimization", {})}
        new_cfg["optimization"]["warehouse_capacity"] = float(self.cfg["optimization"].get("warehouse_capacity", 1e9)) * float(capacity_multiplier)
        if moq_overrides:
            base = new_cfg["optimization"].get("min_order_qty_by_sku", {})
            base = {**base, **moq_overrides}
            new_cfg["optimization"]["min_order_qty_by_sku"] = base
        return df, new_cfg