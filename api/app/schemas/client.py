from pydantic import BaseModel

from app.schemas.common import ORMBase


class ClientCreate(BaseModel):
    name: str
    slug: str


class ClientRead(ORMBase):
    name: str
    slug: str
