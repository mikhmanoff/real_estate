from fastapi import FastAPI
from pydantic import BaseModel
from core.registry import ChannelRegistry
from core.utils import env

app = FastAPI(title="Channel Registry API")
REG = ChannelRegistry(env("CHANNELS_FILE"))

class PublicIn(BaseModel):
    username: str

class InviteIn(BaseModel):
    link: str

@app.get("/channels")
def list_channels():
    return REG.list_all().model_dump()

@app.post("/channels/public")
def add_public(body: PublicIn):
    REG.add_public(body.username)
    return {"ok": True}

@app.post("/channels/invite")
def add_invite(body: InviteIn):
    REG.add_invite(body.link)
    return {"ok": True}

@app.delete("/channels")
def remove(value: str):
    REG.remove(value)
    return {"ok": True}
