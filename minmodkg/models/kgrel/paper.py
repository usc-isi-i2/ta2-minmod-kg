from __future__ import annotations

from minmodkg.misc.utils import makedict
from minmodkg.models.kg.base import NS_MR
from minmodkg.models.kg.paper import Paper as KGPaper
from minmodkg.models.kgrel.base import Base
from minmodkg.typing import InternalID
from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column


class Paper(MappedAsDataclass, Base):
    """A GeoChem paper. Its sites are the mineral sites sharing its source_id."""

    __tablename__ = "paper"

    paper_id: Mapped[str] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(unique=True, index=True)
    doi: Mapped[str] = mapped_column()
    title: Mapped[str | None] = mapped_column()
    authors: Mapped[str | None] = mapped_column()
    journal: Mapped[str | None] = mapped_column()
    year: Mapped[int | None] = mapped_column()
    url: Mapped[str | None] = mapped_column()
    # path of the source JSON-LD, relative to the JSON-LD directory
    file: Mapped[str] = mapped_column()
    modified_at: Mapped[int] = mapped_column(BigInteger)

    def to_kg(self, site_ids: list[InternalID]) -> KGPaper:
        return KGPaper(
            paper_id=self.paper_id,
            paper_title=self.title,
            paper_doi=self.doi,
            paper_authors=self.authors,
            paper_journal=self.journal,
            paper_year=self.year,
            paper_url=self.url,
            has_mineral_site=[NS_MR.uristr(s) for s in site_ids],
        )

    def to_dict(self):
        return makedict.without_none(
            (
                ("paper_id", self.paper_id),
                ("source_id", self.source_id),
                ("doi", self.doi),
                ("title", self.title),
                ("authors", self.authors),
                ("journal", self.journal),
                ("year", self.year),
                ("url", self.url),
                ("file", self.file),
                ("modified_at", self.modified_at),
            )
        )
