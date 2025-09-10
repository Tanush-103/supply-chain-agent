from typing import Dict, Any
import yaml
import pandas as pd


from .types import Message, OrchestratorResponse
from .intent import classify_intent, Intent
from agents.data_retrieval import DataRetrievalAgent
from agents.optimization import OptimizationAgent
from agents.visualization import VisualizationAgent
from agents.what_if import WhatIfAgent

class Orchestrator:
    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path, "r") as f:
            self.cfg = yaml.safe_load(f)
            self.retriever = DataRetrievalAgent(self.cfg)
            self.viz = VisualizationAgent(self.cfg)
            self.optimizer = OptimizationAgent(self.cfg)
            self.whatis = WhatIfAgent(self.cfg)
            self.state: Dict[str, Any] = {"last_merged": None, "last_results": None}


    def handle(self, user_text: str) -> OrchestratorResponse:
        it = classify_intent(user_text)
        msgs = []
        artifacts = {}


        if it == Intent.RETRIEVE:
            data = self.retriever.retrieve(user_text, top_k=self.cfg["retrieval"].get("top_k", 100))
            self.state["last_merged"] = data.get("merged")
            msgs.append(Message(role="assistant", content=f"Retrieved data frames: {', '.join(data.keys())}."))
            artifacts.update({k: (v.head(5).to_dict(orient='records') if isinstance(v, pd.DataFrame) else v) for k, v in data.items()})


        elif it == Intent.OPTIMIZE:
            if self.state.get("last_merged") is None:
                data = self.retriever.retrieve("fast-moving items", top_k=self.cfg["retrieval"].get("top_k", 100))
                self.state["last_merged"] = data.get("merged")
            results, summary = self.optimizer.optimize(self.state["last_merged"])
            self.state["last_results"] = results
            msgs.append(Message(role="assistant", content=f"Optimization complete. Objective={summary['objective']:.2f}. Capacity used={summary['capacity_used']:.1f}/{summary['capacity_limit']:.1f}"))
            artifacts["summary"] = summary
            artifacts["results_preview"] = results.head(10).to_dict(orient='records')


        elif it == Intent.VISUALIZE:
            if self.state.get("last_results") is None:
                msgs.append(Message(role="assistant", content="No optimization results to visualize yet. Run optimization first."))
            else:
                orders = self.viz.plot_orders(self.state["last_results"])
                coverage = self.viz.plot_coverage(self.state["last_results"])
                artifacts["plots"] = {"orders": orders, "coverage": coverage}
                msgs.append(Message(role="assistant", content=f"Charts saved: {orders['plot_path']}, {coverage['plot_path']}"))


        elif it == Intent.WHATIF:
        # crude parse of deltas (e.g., "demand +15% and capacity -10%")
            import re
            dm = 1.0
            cm = 1.0
            md = re.search(r"demand\s*([+-]?[0-9]{1,3})%", user_text.lower())
            if md:
                dm = 1.0 + float(md.group(1))/100.0
            mc = re.search(r"capacity\s*([+-]?[0-9]{1,3})%", user_text.lower())
            if mc:
                cm = 1.0 + float(mc.group(1))/100.0


            if self.state.get("last_merged") is None:
                data = self.retriever.retrieve("fast-moving items")
                self.state["last_merged"] = data.get("merged")
            mod_df, new_cfg = self.whatis.apply(self.state["last_merged"], demand_multiplier=dm, capacity_multiplier=cm)
            # temp override config for one run
            optimizer = OptimizationAgent(new_cfg)
            results, summary = optimizer.optimize(mod_df)
            self.state["last_results"] = results
            msgs.append(Message(role="assistant", content=f"Scenario run complete (demand x{dm:.2f}, capacity x{cm:.2f}). Objective={summary['objective']:.2f}"))
            artifacts["summary"] = summary
            artifacts["results_preview"] = results.head(10).to_dict(orient='records')


        else:
            msgs.append(Message(role="assistant", content="Say things like 'retrieve fast-moving items', 'optimize inventory', 'visualize results', or 'what-if demand +10% and capacity -5%'."))


        return OrchestratorResponse(messages=msgs, artifacts=artifacts)