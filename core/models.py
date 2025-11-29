from pydantic import BaseModel, Field
from typing import List

class Channels(BaseModel):
    public: List[str] = Field(default_factory=list)
    invites: List[str] = Field(default_factory=list)
    resolved_ids: List[int] = Field(default_factory=list)
