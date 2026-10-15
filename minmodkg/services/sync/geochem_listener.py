from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from minmodkg.etl.geochem_jsonld import (
    apply_sample,
    apply_site,
    read_paper_file,
    write_paper_file,
)
from minmodkg.misc.utils import format_datetime, format_nanoseconds
from minmodkg.models.kg.mineral_site import MineralSite as KGMineralSite
from minmodkg.models.kg.sample import EditEvent
from minmodkg.models.kg.sample import Sample as KGSample
from minmodkg.models.kgrel.event import EventLog
from minmodkg.models.kgrel.mineral_site import MineralSiteAndInventory
from minmodkg.models.kgrel.paper import Paper
from minmodkg.models.kgrel.sample import Sample
from minmodkg.services.sync.backup_listener import commit_and_push
from minmodkg.services.sync.geochem_routing import GeoChemRouting
from minmodkg.services.sync.listener import Listener
from minmodkg.typing import InternalID


class GeoChemJournal(Listener):
    """Collects the edits of GeoChem papers' sites and samples, per paper."""

    def handle_begin(self, events: Sequence[EventLog]):
        self.routing = GeoChemRouting()
        self.journal: dict[
            str, list[tuple[KGMineralSite | KGSample, Optional[EditEvent]]]
        ] = defaultdict(list)

    def add(
        self,
        paper: Paper | None,
        item: KGMineralSite | KGSample,
        edit: Optional[EditEvent] = None,
    ) -> None:
        if paper is not None:
            self.journal[paper.paper_id].append((item, edit))

    def handle_site_add(
        self,
        event: EventLog,
        site: MineralSiteAndInventory,
        same_site_ids: list[InternalID],
    ):
        self.add(self.routing.site_paper(site), site.ms.to_kg())

    def handle_site_update(self, event: EventLog, site: MineralSiteAndInventory):
        # a sample keeps its own edit_history; a site's editor and the fields
        # they changed are on the event
        edit = None
        editor = event.data.get("edited_by")
        if editor is not None and event.data.get("changed_fields"):
            edit = EditEvent(
                updated_by=editor,
                updated_at=format_datetime(
                    datetime.fromtimestamp(event.timestamp / 1e9, tz=timezone.utc)
                ),
                changed_properties=event.data["changed_fields"],
            )
        self.add(self.routing.site_paper(site), site.ms.to_kg(), edit)

    def handle_same_as_update(
        self,
        event: EventLog,
        user_uri: str,
        groups: list[list[InternalID]],
        diff_groups: dict[InternalID, list[InternalID]],
    ):
        # same-as links are MinMod's, backed up by its own sync
        pass

    def handle_sample_add(self, event: EventLog, sample: Sample):
        self.add(self.routing.sample_paper(sample), sample.to_kg())

    def handle_sample_update(self, event: EventLog, sample: Sample):
        self.add(self.routing.sample_paper(sample), sample.to_kg())

    def apply(self, paper_id: str, doc: dict) -> None:
        """Apply the paper's collected edits to its JSON-LD document."""
        for item, edit in self.journal.get(paper_id, []):
            if isinstance(item, KGSample):
                apply_sample(doc, item)
            else:
                apply_site(doc, item, edit)


class GeoChemBackupListener(GeoChemJournal):
    """Writes edits of GeoChem papers' sites and samples back into the
    papers' JSON-LD files. Runs apart from MinMod's backup, so a failure here
    only holds up GeoChem write-back."""

    def __init__(self, jsonld_dir: Path):
        super().__init__()
        self.jsonld_dir = jsonld_dir

    def handle_end(self, events: Sequence[EventLog]):
        papers = {p.paper_id: p for p in self.routing.papers.values()}
        for paper_id in self.journal:
            path = self.jsonld_dir / papers[paper_id].file
            doc = read_paper_file(path)
            self.apply(paper_id, doc)
            write_paper_file(path, doc)

        if self.journal and (self.jsonld_dir / ".git").exists():
            commit_and_push(
                self.jsonld_dir,
                f"Backup edits as of {format_nanoseconds(events[-1].timestamp)}",
            )
