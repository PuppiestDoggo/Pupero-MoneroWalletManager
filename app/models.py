from sqlmodel import SQLModel, Field, UniqueConstraint
from typing import Optional
from datetime import datetime

class AddressMap(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    address: str = Field(index=True, max_length=128)
    label: Optional[str] = Field(default=None, max_length=255)
    account_index: int = Field(default=0, index=True)
    address_index: int = Field(default=0, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("address", name="uq_addressmap_address"),
    )
