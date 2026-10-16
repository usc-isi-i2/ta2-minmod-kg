from __future__ import annotations

from typing import Optional

from minmodkg.models.kg.entities.state_or_province import (
    StateOrProvince as KGStateOrProvince,
)
from minmodkg.models.kgrel.base import Base
from minmodkg.typing import InternalID
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column


class StateOrProvince(MappedAsDataclass, Base):
    __tablename__ = "state_or_province"

    id: Mapped[InternalID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column()
    country: Mapped[Optional[InternalID]] = mapped_column(
        ForeignKey("country.id", ondelete="CASCADE")
    )
    # ISO 3166-2 subdivision code, unique within a country ("CO" -> Colorado);
    # only used to repair state assignments, not exported to the KG.
    state_code: Mapped[Optional[str]] = mapped_column(default=None)

    @property
    def uri(self):
        return KGStateOrProvince.__subj__.key_ns.uristr(self.id)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "country": self.country,
            "state_code": self.state_code,
        }

    @classmethod
    def from_dict(cls, data: dict) -> StateOrProvince:
        return cls(
            id=data["id"],
            name=data["name"],
            country=data["country"],
            state_code=data.get("state_code"),
        )

    def to_kg(self) -> KGStateOrProvince:
        return KGStateOrProvince(
            id=self.id,
            name=self.name,
            country=self.country,
        )
