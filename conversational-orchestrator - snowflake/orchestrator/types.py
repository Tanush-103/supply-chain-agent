from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class Message(BaseModel):
    role: str # "user" | "system" | "assistant"
    content: str


class RetrievalRequest(BaseModel):
    query: str
    top_k: int = 100


class OptimizationRequest(BaseModel):
    scenario_name: str = "base"
    service_level: float = 0.95
    warehouse_capacity: float
    ordering_cost_per_order: float
    holding_cost_per_unit: float
    stockout_penalty_per_unit: float
    min_order_qty_by_sku: Dict[str, float] = {}
    max_order_qty_by_sku: Dict[str, float] = {}
    business_rules: Dict[str, Any] = {}


class WhatIfRequest(BaseModel):
    demand_multiplier: float = 1.0
    capacity_multiplier: float = 1.0
    moq_overrides: Dict[str, float] = {}


class VisualizationRequest(BaseModel):
    title: str = "Optimization Results"


class OrchestratorResponse(BaseModel):
    messages: List[Message]
    artifacts: Dict[str, Any] = Field(default_factory=dict)