from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Literal, Optional, Sequence

import serde.csv
import serde.json
import xxhash
from minmodkg.etl.geochem_jsonld import (
    USER_URI,
    apply_sample,
    apply_site,
    read_paper_file,
    write_paper_file,
)
from minmodkg.misc.utils import format_nanoseconds
from minmodkg.models.kg.mineral_site import MineralSite as KGMineralSite
from minmodkg.models.kg.sample import Sample as KGSample
from minmodkg.models.kgrel.base import engine
from minmodkg.models.kgrel.data_source import DataSource
from minmodkg.models.kgrel.event import EventLog
from minmodkg.models.kgrel.mineral_site import MineralSite, MineralSiteAndInventory
from minmodkg.models.kgrel.paper import Paper
from minmodkg.models.kgrel.sample import Sample
from minmodkg.models.kgrel.user import get_username
from minmodkg.services.kgrel_entity import EntityService
from minmodkg.services.sync.listener import Listener
from minmodkg.typing import InternalID
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.orm import Session

from statickg.models.repository import GitRepository


class BackupListener(Listener):
    def __init__(self, data_repo_dir: Path, jsonld_dir: Optional[Path] = None):
        super().__init__()
        self.data_repo_dir = data_repo_dir
        # GeoChem papers are backed up into their own JSON-LD files, which are
        # the source of truth for the GeoChem loader
        self.jsonld_dir = jsonld_dir

    def handle_begin(self, events: Sequence[EventLog]):
        self.site_journal: dict[tuple, list[tuple[Literal["add", "update"], dict]]] = (
            defaultdict(list)
        )
        self.same_as_journal: dict[
            str, list[tuple[InternalID, InternalID, int, int]]
        ] = defaultdict(list)
        # keyed by parent mineral_site_id only -- Sample has no created_by/source_id
        # of its own to bucket by like MineralSite does, so this just mirrors the
        # parent-site relationship that already exists.
        self.sample_journal: dict[
            InternalID, list[tuple[Literal["add", "update"], dict]]
        ] = defaultdict(list)
        self.paper_journal: dict[str, list[KGMineralSite | KGSample]] = defaultdict(
            list
        )
        with Session(engine) as session:
            self.papers = {
                p.source_id: p for p in session.execute(select(Paper)).scalars()
            }
        self.site_papers: dict[InternalID, Optional[str]] = {}

    def handle_site_add(
        self,
        event: EventLog,
        site: MineralSiteAndInventory,
        same_site_ids: list[InternalID],
    ):
        if not self._journal_paper_site(site):
            self._upsert_site("add", site)
        self._update_same_as(
            site.ms.created_by,
            [[site.ms.site_id] + same_site_ids],
            {},
            site.ms.modified_at,
        )

    def handle_site_update(self, event: EventLog, site: MineralSiteAndInventory):
        if not self._journal_paper_site(site):
            self._upsert_site("update", site)

    def handle_same_as_update(
        self,
        event: EventLog,
        user_uri: str,
        groups: list[list[InternalID]],
        diff_groups: dict[InternalID, list[InternalID]],
    ):
        # write the same as links to the journal
        # there will be a single same-as file for all users
        self._update_same_as(user_uri, groups, diff_groups, event.timestamp)

    def handle_sample_add(self, event: EventLog, sample: Sample):
        if not self._journal_paper_sample(sample):
            self._upsert_sample("add", sample)

    def handle_sample_update(self, event: EventLog, sample: Sample):
        if not self._journal_paper_sample(sample):
            self._upsert_sample("update", sample)

    def handle_end(self, events: Sequence[EventLog]):
        for (username, source_name, bucket_no), actions in self.site_journal.items():
            outfile = (
                self.data_repo_dir
                / f"data/mineral-sites/{PartitionFn.get_filename(username, source_name, bucket_no)}"
            )
            if outfile.exists():
                sites = serde.json.deser(outfile)
                id2index = {r["record_id"]: i for i, r in enumerate(sites)}
            else:
                sites = []
                id2index = {}

            for action, site in actions:
                if site["record_id"] not in id2index:
                    sites.append(site)
                    id2index[site["record_id"]] = len(sites) - 1

                if action == "add":
                    # do nothing
                    pass
                else:
                    assert action == "update"
                    sites[id2index[site["record_id"]]] = site

            outfile.parent.mkdir(parents=True, exist_ok=True)
            serde.json.ser(sites, outfile, indent=2)

        for mineral_site_id, actions in self.sample_journal.items():
            outfile = (
                self.data_repo_dir / f"data/geochem-samples/{mineral_site_id}.json"
            )
            if outfile.exists():
                samples = serde.json.deser(outfile)
                id2index = {r["id"]: i for i, r in enumerate(samples)}
            else:
                samples = []
                id2index = {}

            for action, sample in actions:
                if sample["id"] not in id2index:
                    samples.append(sample)
                    id2index[sample["id"]] = len(samples) - 1

                if action == "add":
                    # do nothing
                    pass
                else:
                    assert action == "update"
                    samples[id2index[sample["id"]]] = sample

            outfile.parent.mkdir(parents=True, exist_ok=True)
            serde.json.ser(samples, outfile, indent=2)

        for username, same_as_links in self.same_as_journal.items():
            outfile = self.data_repo_dir / f"data/same-as/{username}/same_as.csv"
            if outfile.exists():
                records = serde.csv.deser(outfile)
                assert records[0] == ["ms_1", "ms_2", "time_ns", "is_same"]
                if len(records) > 1:
                    assert records[1] != ["ms_1", "ms_2", "time_ns", "is_same"]
                records = records[1:]  # skip header
                key2idx = {(r[0], r[1]): i for i, r in enumerate(records)}
            else:
                records = []
                key2idx = {}

            delete_indices = set()
            for s, o, ts, is_same in same_as_links:
                key = (s, o)
                if key in key2idx:
                    delete_indices.add(key2idx[key])
                records.append([s, o, str(ts), str(is_same)])
                key2idx[key] = len(records) - 1

            output = [["ms_1", "ms_2", "time_ns", "is_same"]]
            output.extend((r for i, r in enumerate(records) if i not in delete_indices))
            outfile.parent.mkdir(parents=True, exist_ok=True)
            if len(output) > 1:
                serde.csv.ser(output, outfile)

        for source_id, items in self.paper_journal.items():
            assert self.jsonld_dir is not None
            path = self.jsonld_dir / self.papers[source_id].file
            doc = read_paper_file(path)
            for item in items:
                if isinstance(item, KGSample):
                    apply_sample(doc, item)
                else:
                    apply_site(doc, item)
            write_paper_file(path, doc)

        if (
            len(self.paper_journal) > 0
            and self.jsonld_dir is not None
            and (self.jsonld_dir / ".git").exists()
        ):
            GitRepository(self.jsonld_dir).commit_all(
                f"Backup edits as of {format_nanoseconds(events[-1].timestamp)}"
            ).push()

        if len(events) > 0:
            # after updating the files, we need to commit the changes to the git repo
            GitRepository(self.data_repo_dir).commit_all(
                f"Backup data as of {format_nanoseconds(events[-1].timestamp)}"
            ).push()

    def _journal_paper_site(self, site: MineralSiteAndInventory) -> bool:
        """Journal a site that belongs to a GeoChem paper; False otherwise."""
        if site.ms.created_by != USER_URI or site.ms.source_id not in self.papers:
            return False
        self._require_jsonld_dir()
        self.site_papers[site.ms.site_id] = site.ms.source_id
        self.paper_journal[site.ms.source_id].append(site.ms.to_kg())
        return True

    def _journal_paper_sample(self, sample: Sample) -> bool:
        """Journal a sample whose site belongs to a GeoChem paper; False otherwise."""
        site_id = sample.mineral_site_id
        if site_id not in self.site_papers:
            with Session(engine) as session:
                row = session.execute(
                    select(MineralSite.source_id, MineralSite.created_by).where(
                        MineralSite.site_id == site_id
                    )
                ).one_or_none()
            self.site_papers[site_id] = (
                row[0]
                if row is not None and row[1] == USER_URI and row[0] in self.papers
                else None
            )
        source_id = self.site_papers[site_id]
        if source_id is None:
            return False
        self._require_jsonld_dir()
        self.paper_journal[source_id].append(sample.to_kg())
        return True

    def _require_jsonld_dir(self) -> None:
        if self.jsonld_dir is None:
            raise ValueError(
                "GeoChem paper edits need the JSON-LD directory (--jsonld-dir)"
            )

    def _upsert_site(
        self, action: Literal["add", "update"], site: MineralSiteAndInventory
    ):
        username = get_username(site.ms.created_by)

        source_id = site.ms.source_id
        lst = source_id.split("::")
        if len(lst) > 1:
            source_id = lst[1]

        data_sources = EntityService.get_instance().get_data_sources()
        if source_id not in data_sources:
            data_sources = EntityService.get_instance().get_data_sources(refresh=True)

        # determine the bucket that we are going to write the data to.
        # (username, data source, bucket number)
        bucket_no = PartitionFn.get_bucket_no(site.ms.record_id)
        if source_id not in data_sources:
            source_name = "unknown"
        else:
            source_name = data_sources[source_id].slug_name
        key = (username, source_name, bucket_no)

        self.site_journal[key].append((action, site.ms.to_kg().to_dict()))

    def _upsert_sample(self, action: Literal["add", "update"], sample: Sample):
        # to_dict() doesn't include the computed public_id (it's a cached_property,
        # not a P()-annotated field -- see SampleIdent.id), so add it explicitly as
        # the record's own merge key, mirroring how MineralSite's dict already has
        # record_id available naturally.
        record = sample.to_kg().to_dict()
        record["id"] = sample.public_id
        self.sample_journal[sample.mineral_site_id].append((action, record))

    def _update_same_as(
        self,
        user_uri: str,
        groups: list[list[InternalID]],
        diff_groups: dict[InternalID, list[InternalID]],
        timestamp: int,
    ):
        username = get_username(user_uri)
        records = []
        for group in groups:
            for target in group[1:]:
                records.append((group[0], target, timestamp, 1))
        for site_id, diff_sites in diff_groups.items():
            for diff_site in diff_sites:
                records.append((site_id, diff_site, timestamp, 0))
        self.same_as_journal[username].extend(records)


class PartitionFn:
    """Partition the mineral sites from a single file into <source_id>/<bucket>/<file_name>.json.

    With the restructured Github, this function is no longer needed and is deprecated.
    """

    instances = {}
    num_buckets = 64

    @staticmethod
    def get_bucket_no(record_id: str) -> int:
        enc_record_id = slugify(str(record_id).strip()).encode()
        bucketno = xxhash.xxh64(enc_record_id).intdigest() % PartitionFn.num_buckets
        return bucketno

    @staticmethod
    def get_filename(username: str, source_name: str, bucket_no: int) -> Path:
        return Path(f"{username}/{source_name}/b{bucket_no:03d}.json")
