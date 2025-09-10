

Orchestrator (Conversational Controller)

Parses user requests → classifies intent (retrieve / optimize / visualize / what‑if)

Collects required inputs from Data Retrieval Agent

Applies SME business rules while building the model via Optimization Agent

Delegates result rendering to Visualization Agent

Keeps conversation state (context) and scenario cache

Multimodal Data Retrieval Agent

Pulls from SQL and/or CSV/Text

Uses semantic matching to map fuzzy user requests (e.g., "fast‑moving items") to concrete fields

Consolidates inventory, demand, supplier, and cost metrics into unified frames ready for modeling

Optimization Modeling Agent

Builds a linear program (via PuLP) for replenishment planning

Embeds business rules:

Priority SKUs → scaled stockout penalties or service targets

Supplier lead times → safety stock and cycle stock constraints

Min/max order quantities, capacity limits

Visualization Agent

Produces Plotly charts (saved to outputs/) & returns file paths/JSON spec

What‑If Agent

Applies scenario deltas (e.g., demand +15%, capacity −10%, MOQ changes) and re‑runs optimization