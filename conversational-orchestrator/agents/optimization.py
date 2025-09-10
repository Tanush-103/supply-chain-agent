# agents/optimization.py (replace the optimize() function body)
from typing import Dict, Any, Tuple
import pandas as pd
import numpy as np
from typing import Dict, Any, Tuple
import pandas as pd
import numpy as np
import pulp as pl


# from tools.business_rules import BusinessRules
# keep your existing imports at the top of the file:
from tools.business_rules import BusinessRules
# (and keep the OptimizationAgent.__init__ and _prepare methods as-is)

def _as_float(x):
    try:
        return float(x)
    except Exception:
        return 0.0

class OptimizationAgent:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.rules = BusinessRules(cfg.get("business_rules", {}))

    def _prepare(self, merged: pd.DataFrame) -> pd.DataFrame:
        # ... keep your existing _prepare code unchanged ...
        df = merged.copy()
        sl = float(self.cfg["optimization"].get("service_level", 0.95))
        Z = 1.65 if sl >= 0.95 else 1.28
        df["lead_time_days"] = df.apply(
            lambda r: r.get("lead_time_days")
            if pd.notnull(r.get("lead_time_days"))
            else self.rules.get_supplier_lead(r.get("supplier")),
            axis=1,
        )
        df["demand_std"].fillna(0.0, inplace=True)
        df["safety_stock"] = Z * df["demand_std"] * np.sqrt((df["lead_time_days"]) / 30.0)
        df["holding_cost"] = float(self.cfg["optimization"].get("holding_cost_per_unit", 0.02))
        df["stockout_penalty"] = float(self.cfg["optimization"].get("stockout_penalty_per_unit", 5.0))
        mult = self.rules.apply_priority_weights(df)
        df["stockout_penalty"] = df["stockout_penalty"] * mult
        df["per_unit_transport_cost"].fillna(0.0, inplace=True)
        df["unit_order_cost"] = df["per_unit_transport_cost"]
        return df

    def optimize(self, merged: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        from ortools.linear_solver import pywraplp

        params = self.cfg["optimization"]
        capacity = float(params.get("warehouse_capacity", 1e9))
        ordering_cost = float(params.get("ordering_cost_per_order", 100.0))
        min_by_sku = params.get("min_order_qty_by_sku", {})
        max_by_sku = params.get("max_order_qty_by_sku", {})

        df = self._prepare(merged).reset_index(drop=True)
        skus = df["sku"].tolist()

        # OR-Tools CBC is in-process via pywraplp on Windows
        solver = pywraplp.Solver.CreateSolver("CBC_MIXED_INTEGER_PROGRAMMING")
        if solver is None:
            raise RuntimeError("Failed to create OR-Tools CBC solver.")

        # Decision vars
        q = {}  # order quantity >= 0 (continuous)
        y = {}  # order indicator in {0,1}
        u = {}  # safety shortfall >= 0 (continuous)
        BIG_M = 1e6

        for i, s in enumerate(skus):
            q[s] = solver.NumVar(0.0, solver.infinity(), f"q_{i}")
            y[s] = solver.BoolVar(f"y_{i}")
            u[s] = solver.NumVar(0.0, solver.infinity(), f"u_{i}")

        # Objective components
        holding = []
        transport = []
        ordering_fixed = []
        penalty_terms = []

        for s in skus:
            row = df.loc[df["sku"] == s].iloc[0]
            soh = _as_float(row["stock_on_hand"])
            dem = _as_float(row["demand_mean"])
            ss  = _as_float(row["safety_stock"])
            hold_cost = _as_float(row["holding_cost"])
            unit_order_cost = _as_float(row["unit_order_cost"])
            stockout_pen = _as_float(row["stockout_penalty"])

            holding.append( hold_cost * (soh + q[s]) )
            transport.append( unit_order_cost * q[s] )
            ordering_fixed.append( ordering_cost * y[s] )

            # u[s] >= ss - (soh + q[s] - dem)
            ct = solver.Constraint(0, solver.infinity())
            ct.SetCoefficient(u[s], 1.0)
            ct.SetCoefficient(q[s], 1.0)     # move q[s] to LHS with +1 and constants to RHS
            # Rearranged: u - q >= ss - soh + dem
            rhs = ss - soh + dem
            ct.SetBounds(rhs, solver.infinity())

            penalty_terms.append( stockout_pen * u[s] )

        # Capacity: sum(unit_volume * q) <= capacity
        cap_ct = solver.Constraint(-solver.infinity(), capacity)
        for s in skus:
            vol = _as_float(df.loc[df["sku"] == s, "unit_volume"].values[0])
            cap_ct.SetCoefficient(q[s], vol)

        # MOQ & linking: q <= M*y ; q >= moq*y ; max_by_sku if provided
        for s in skus:
            # q <= M*y
            ct1 = solver.Constraint(-solver.infinity(), 0.0)
            ct1.SetCoefficient(q[s], 1.0)
            ct1.SetCoefficient(y[s], -BIG_M)

            # q >= moq*y  -> q - moq*y >= 0
            moq_default = 0.0
            moq = float(min_by_sku.get(s, moq_default))
            if moq > 0:
                ct2 = solver.Constraint(0.0, solver.infinity())
                ct2.SetCoefficient(q[s], 1.0)
                ct2.SetCoefficient(y[s], -moq)

            # q <= max_by_sku
            if s in max_by_sku:
                maxq = float(max_by_sku[s])
                ct3 = solver.Constraint(-solver.infinity(), maxq)
                ct3.SetCoefficient(q[s], 1.0)

        # Objective
        objective = solver.Objective()
        for term in holding + transport + ordering_fixed + penalty_terms:
            objective.SetOffset(objective.Offset() + 0.0)  # no-op for clarity
        # Add terms explicitly
        for s in skus:
            row = df.loc[df["sku"] == s].iloc[0]
            soh = _as_float(row["stock_on_hand"])
            dem = _as_float(row["demand_mean"])
            ss  = _as_float(row["safety_stock"])
            hold_cost = _as_float(row["holding_cost"])
            unit_order_cost = _as_float(row["unit_order_cost"])
            stockout_pen = _as_float(row["stockout_penalty"])

            # holding: hold_cost * (soh + q)
            objective.SetCoefficient(q[s], objective.GetCoefficient(q[s]) + hold_cost)
            # transport
            objective.SetCoefficient(q[s], objective.GetCoefficient(q[s]) + unit_order_cost)
            # ordering fixed
            objective.SetCoefficient(y[s], objective.GetCoefficient(y[s]) + ordering_cost)
            # penalty for shortfall u
            objective.SetCoefficient(u[s], objective.GetCoefficient(u[s]) + stockout_pen)

        objective.SetMinimization()

        # Solve
        status = solver.Solve()
        if status not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
            raise RuntimeError(f"OR-Tools solver status {status}")

        # Build output
        df_out = df[["sku","description","stock_on_hand","demand_mean","safety_stock","unit_volume","supplier"]].copy()
        df_out["order_qty"] = [q[s].solution_value() for s in skus]
        df_out["ordered"] = [int(round(y[s].solution_value())) for s in skus]
        df_out["safety_shortfall"] = [max(0.0, u[s].solution_value()) for s in skus]

        summary = {
            "objective": objective.Value(),
            "capacity_used": float(sum(_as_float(df.loc[df.sku==s, "unit_volume"].values[0]) * q[s].solution_value() for s in skus)),
            "capacity_limit": capacity,
            "solver": "OR-Tools CBC (in-process)",
        }
        return df_out.sort_values("order_qty", ascending=False), summary




# from typing import Dict, Any, Tuple
# import pandas as pd
# import numpy as np
# import pulp as pl


# from tools.business_rules import BusinessRules

# def _as_float(x):
#     try:
#         return float(x)
#     except Exception:
#         return 0.0

# class OptimizationAgent:
#     def __init__(self, cfg: Dict[str, Any]):
#         self.cfg = cfg
#         self.rules = BusinessRules(cfg.get("business_rules", {}))


#     def _prepare(self, merged: pd.DataFrame) -> pd.DataFrame:
#         df = merged.copy()
#         # Safety stock heuristic using lead time and demand std (Z for service level)
#         sl = float(self.cfg["optimization"].get("service_level", 0.95))
#         Z = 1.65 if sl >= 0.95 else 1.28
#         df["lead_time_days"] = df.apply(lambda r: r.get("lead_time_days") if pd.notnull(r.get("lead_time_days")) else self.rules.get_supplier_lead(r.get("supplier")), axis=1)
#         df["demand_std"].fillna(0.0, inplace=True)
#         df["safety_stock"] = Z * df["demand_std"] * np.sqrt((df["lead_time_days"]) / 30.0)
#         df["holding_cost"] = float(self.cfg["optimization"].get("holding_cost_per_unit", 0.02))
#         df["stockout_penalty"] = float(self.cfg["optimization"].get("stockout_penalty_per_unit", 5.0))
#         # Priority scaling
#         mult = self.rules.apply_priority_weights(df)
#         df["stockout_penalty"] = df["stockout_penalty"] * mult
#         # transport cost (optional) folded into cost objective per unit ordered
#         df["per_unit_transport_cost"].fillna(0.0, inplace=True)
#         df["unit_order_cost"] = df["per_unit_transport_cost"]
#         return df


#     def optimize(self, merged: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
#         params = self.cfg["optimization"]
#         capacity = float(params.get("warehouse_capacity", 1e9))
#         ordering_cost = float(params.get("ordering_cost_per_order", 100.0))
#         min_by_sku = params.get("min_order_qty_by_sku", {})
#         max_by_sku = params.get("max_order_qty_by_sku", {})


#         df = self._prepare(merged)
#         skus = df["sku"].tolist()


#         # Decision variables
#         model = pl.LpProblem("InventoryReplenishment", pl.LpMinimize)
#         q = pl.LpVariable.dicts("order_qty", skus, lowBound=0)
#         y = pl.LpVariable.dicts("order_bool", skus, lowBound=0, upBound=1, cat=pl.LpBinary)


#         # Objective: holding + transport (per-unit) + ordering fixed + stockout penalty if below safety stock post-order
#         holding = pl.lpSum([df.loc[df.sku==s, "holding_cost"].values[0] * (df.loc[df.sku==s, "stock_on_hand"].values[0] + q[s]) for s in skus])
#         transport = pl.lpSum([df.loc[df.sku==s, "unit_order_cost"].values[0] * q[s] for s in skus])
#         ordering_fixed = pl.lpSum([ordering_cost * y[s] for s in skus])


#         # Soft stockout penalty (below safety after meeting one-period demand mean)
#         penalty_terms = []
#         for s in skus:
#             soh = df.loc[df.sku==s, "stock_on_hand"].values[0]
#             dem = df.loc[df.sku==s, "demand_mean"].values[0]
#             ss = df.loc[df.sku==s, "safety_stock"].values[0]
#             penalty = df.loc[df.sku==s, "stockout_penalty"].values[0]
#         # unmet_safety = max(0, ss - (soh + q - dem))
#         # approximate with linearization using surplus var u_s ≥ ss - (soh + q - dem)
#         u = pl.LpVariable.dicts("safety_shortfall", skus, lowBound=0)
#         for s in skus:
#             soh = df.loc[df.sku==s, "stock_on_hand"].values[0]
#             dem = df.loc[df.sku==s, "demand_mean"].values[0]
#             ss = df.loc[df.sku==s, "safety_stock"].values[0]
#             model += u[s] >= ss - (soh + q[s] - dem)
#             penalty = df.loc[df.sku==s, "stockout_penalty"].values[0]
#             penalty_terms.append(penalty * u[s])


#         stockout_penalty = pl.lpSum(penalty_terms)


#         model += holding + transport + ordering_fixed + stockout_penalty


#         # Linking order_bool and quantity (if y=0 then q=0; if y=1 then q≥MOQ)
#         for s in skus:
#             moq_default = 0.0
#             moq = float(min_by_sku.get(s, moq_default))
#             M = 1e6
#             model += q[s] <= M * y[s]
#             model += q[s] >= moq * y[s]
#             # max bounds if provided
#             if s in max_by_sku:
#                 model += q[s] <= float(max_by_sku[s])


#         # Capacity constraint
#         model += pl.lpSum([df.loc[df.sku==s, "unit_volume"].values[0] * q[s] for s in skus]) <= capacity


#         # Solve
#         model.solve(pl.PULP_CBC_CMD(msg=False))


#         df_out = df[["sku", "description", "stock_on_hand", "demand_mean", "safety_stock", "unit_volume", "supplier"]].copy()
#         df_out["order_qty"] = [q[s].value() for s in skus]
#         df_out["ordered"] = [int(round(y[s].value() or 0)) for s in skus]
#         df_out["safety_shortfall"] = [max(0.0, u[s].value()) for s in skus]


#         summary = {
#         "objective": pl.value(model.objective),
#         "capacity_used": float(sum(df.loc[df.sku==s, "unit_volume"].values[0] * (q[s].value() or 0) for s in skus)),
#         "capacity_limit": capacity,
#         }
#         return df_out.sort_values("order_qty", ascending=False), summary