from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from minmodkg.misc.elements import element_symbol
from minmodkg.models.kgrel.base import Base
from minmodkg.typing import InternalID
from sqlalchemy import ForeignKey, Index, delete, insert
from sqlalchemy.orm import Mapped, MappedAsDataclass, Session, mapped_column

if TYPE_CHECKING:
    from minmodkg.models.kgrel.sample import Sample


class SampleElement(MappedAsDataclass, Base):
    """The elements a sample has readings for, searchable by SQL (analyses
    are stored as an opaque blob). Derived from the sample, never edited."""

    __tablename__ = "sample_element"
    __table_args__ = (
        Index(
            "ix_sample_element_site_id_element_is_bdl",
            "site_id",
            "element",
            "is_bdl",
        ),
    )

    sample_id: Mapped[int] = mapped_column(
        ForeignKey("sample.id", ondelete="CASCADE"), primary_key=True
    )
    site_id: Mapped[InternalID] = mapped_column()
    element: Mapped[str] = mapped_column(primary_key=True)
    # below the detection limit, not a detected value
    is_bdl: Mapped[bool] = mapped_column(primary_key=True)

    @staticmethod
    def from_sample(sample: Sample) -> list[SampleElement]:
        """A row per element and reading kind; none for a deleted sample.

        A grade > 0 is detected; a negative one is below detection (exports
        write "<0.6" as -0.6). No or zero grade is a blank and gets no row."""
        if sample.is_deleted:
            return []
        keys: set[tuple[str, bool]] = set()
        for analysis in sample.analyses:
            if analysis.is_deleted:
                continue
            for elem in analysis.elements:
                if elem.is_deleted:
                    continue
                symbol = element_symbol(elem.label)
                if symbol is None:
                    continue
                if elem.grade is not None and elem.grade != 0:
                    keys.add((symbol, elem.grade < 0))
        return [
            SampleElement(
                sample_id=sample.id,
                site_id=sample.mineral_site_id,
                element=symbol,
                is_bdl=is_bdl,
            )
            for symbol, is_bdl in sorted(keys)
        ]


def replace_sample_elements(session: Session, samples: Iterable[Sample]) -> None:
    """Rewrite the rows of these samples, which must have ids (flushed)."""
    samples = list(samples)
    if not samples:
        return
    ids = [s.id for s in samples]
    # batched: psycopg caps a statement at 65535 parameters
    for i in range(0, len(ids), 10000):
        session.execute(
            delete(SampleElement).where(SampleElement.sample_id.in_(ids[i : i + 10000]))
        )
    rows = [
        {
            "sample_id": r.sample_id,
            "site_id": r.site_id,
            "element": r.element,
            "is_bdl": r.is_bdl,
        }
        for s in samples
        for r in SampleElement.from_sample(s)
    ]
    if rows:
        session.execute(insert(SampleElement), rows)
