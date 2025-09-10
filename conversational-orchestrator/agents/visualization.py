import os
from typing import Dict, Any
import pandas as pd
import plotly.express as px



class VisualizationAgent:
    def __init__(self, cfg: Dict[str, Any]):
        self.outdir = cfg["app"].get("output_dir", "outputs")
        os.makedirs(self.outdir, exist_ok=True)


    def plot_orders(self, results: pd.DataFrame, title: str = "Recommended Orders") -> Dict[str, Any]:
        top = results.sort_values("order_qty", ascending=False).head(30)
        fig = px.bar(top, x="sku", y="order_qty", hover_data=["description", "stock_on_hand", "demand_mean", "safety_stock"], title=title)
        path = os.path.join(self.outdir, "orders_bar.html")
        fig.write_html(path, include_plotlyjs="cdn")
        return {"plot_path": path, "count": len(results)}


    def plot_coverage(self, results: pd.DataFrame, title: str = "Stock vs Safety") -> Dict[str, Any]:
        df = results.copy()
        df["post_order_stock"] = df["stock_on_hand"] + df["order_qty"] - df["demand_mean"]
        fig = px.scatter(df, x="sku", y="post_order_stock", size="order_qty", color=(df["safety_shortfall"] > 0), title=title, hover_data=["description", "safety_stock"])
        path = os.path.join(self.outdir, "coverage_scatter.html")
        fig.write_html(path, include_plotlyjs="cdn")
        return {"plot_path": path, "count": len(results)}



    def figure_orders(results):
        top = results.sort_values("order_qty", ascending=False).head(30)
        return px.bar(top, x="sku", y="order_qty",
                    hover_data=["description","stock_on_hand","demand_mean","safety_stock"],
                    title="Recommended Orders (Top 30)")

    def figure_coverage(results):
        df = results.copy()
        df["post_order_stock"] = df["stock_on_hand"] + df["order_qty"] - df["demand_mean"]
        return px.scatter(df, x="sku", y="post_order_stock", size="order_qty",
                        color=(df["safety_shortfall"] > 0),
                        hover_data=["description","safety_stock"],
                        title="Post-Order Stock vs Safety")
