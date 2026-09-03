from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from minmodkg.misc.utils import format_datetime
from minmodkg.models.kg.base import NS_GCO
from minmodkg.models.kg.sample import EditEvent
from minmodkg.models.kgrel.base import engine
from minmodkg.models.kgrel.event import EventLog
from minmodkg.models.kgrel.sample import Sample
from minmodkg.transformations import make_sample_id
from minmodkg.typing import InternalID
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

# fields that are either internal bookkeeping or not real ontology properties on
# :Sample -- never treated as a "changed property" in an EditEvent
_NON_PROPERTY_FIELDS = {"id", "public_id", "mineral_site_id", "edit_history", "modified_at"}


class ArgumentError(Exception):
    pass


class SampleNotFoundError(Exception):
    pass


class ExpiredSnapshotIdError(Exception):
    pass


class SampleService:

    def __init__(self, _engine: Optional[Engine] = None):
        self.engine = _engine or engine

    def get_sample_db_id(self, public_id: InternalID) -> Optional[int]:
        q = select(Sample.id).where(Sample.public_id == public_id)
        with Session(self.engine) as session:
            return session.execute(q).scalar_one_or_none()

    def find_by_id(self, public_id: InternalID) -> Optional[Sample]:
        q = select(Sample).where(Sample.public_id == public_id)
        with Session(self.engine, expire_on_commit=False) as session:
            return session.execute(q).scalar_one_or_none()

    def create(self, sample: Sample, user_uri: str) -> Sample:
        """Create a new sample. sample.public_id/edit_history/modified_at are
        computed here -- not trusted from the caller (see InputPublicSample.to_kgrel,
        which deliberately leaves them as placeholders)."""
        if not sample.sample_id:
            raise ArgumentError(
                "sample_id is required to compute this Sample's identifier/URI "
                "(the ontology's :sample_id is technically optional, but this API "
                "requires it in practice, same as MineralSite requires source_id/record_id)"
            )

        sample.public_id = make_sample_id(sample.mineral_site_id, sample.sample_id)
        now_ns = time.time_ns()
        sample.modified_at = now_ns
        sample.edit_history = [
            EditEvent(
                updated_by=user_uri,
                updated_at=format_datetime(datetime.now(timezone.utc)),
                changed_properties=self._changed_properties({}, sample.to_dict()),
            )
        ]

        with Session(self.engine, expire_on_commit=False) as session:
            session.add(sample)
            session.add(EventLog.from_sample_add(sample))
            session.commit()
            session.refresh(sample)
        return sample

    def update(
        self,
        sample: Sample,
        user_uri: str,
        snapshot_id: Optional[int] = None,
    ) -> Sample:
        """Update an existing sample (sample.id must already be set -- see
        set_id() -- and sample.public_id must match the existing row's, checked by
        the router before calling this). Appends one EditEvent to the existing
        edit_history; never overwrites it (see EditEvent's docstring)."""
        with Session(self.engine, expire_on_commit=False) as session:
            existing = session.execute(
                select(Sample).where(Sample.id == sample.id)
            ).scalar_one_or_none()
            if existing is None:
                raise SampleNotFoundError(f"Sample {sample.id} does not exist")

            if snapshot_id is not None and existing.modified_at != snapshot_id:
                raise ExpiredSnapshotIdError(
                    f"The new snapshot of the sample is {existing.modified_at}"
                )

            changed = self._changed_properties(existing.to_dict(), sample.to_dict())

            sample.edit_history = existing.edit_history + [
                EditEvent(
                    updated_by=user_uri,
                    updated_at=format_datetime(datetime.now(timezone.utc)),
                    changed_properties=changed,
                )
            ]
            sample.modified_at = time.time_ns()

            session.execute(sample.get_update_query())
            session.add(EventLog.from_sample_update(sample))
            session.commit()
        return sample

    @staticmethod
    def _changed_properties(old: dict, new: dict) -> list[str]:
        keys = (set(old.keys()) | set(new.keys())) - _NON_PROPERTY_FIELDS
        changed = sorted(k for k in keys if old.get(k) != new.get(k))
        return [NS_GCO.uristr(k) for k in changed]
