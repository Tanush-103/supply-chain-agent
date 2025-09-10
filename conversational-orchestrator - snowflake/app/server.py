from fastapi import FastAPI
from pydantic import BaseModel
from orchestrator.orchestrator import Orchestrator


app = FastAPI(title="Supply Chain Orchestrator")
orc = Orchestrator()


class Query(BaseModel):
    text: str


@app.post("/chat")
async def chat(q: Query):
    resp = orc.handle(q.text)
    return resp.dict()

@app.get("/")
async def root():
    return {"status": "ok", "endpoints": ["/chat"]}