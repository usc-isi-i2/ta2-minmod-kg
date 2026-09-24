from __future__ import annotations

from typing import Sequence

import typer
from minmodkg.models.kgrel.event import EventLog
from minmodkg.models.kgrel.mineral_site import MineralSiteAndInventory
from minmodkg.models.kgrel.sample import Sample
from minmodkg.typing import InternalID


class Listener:

    def handle(self, events: Sequence[EventLog]):
        self.handle_begin(events)

        for event in events:
            if event.type == "site:add":
                self.handle_site_add(
                    event,
                    MineralSiteAndInventory.from_dict(event.data["site"]),
                    event.data["same_site_ids"],
                )
            elif event.type == "site:update":
                self.handle_site_update(
                    event, MineralSiteAndInventory.from_dict(event.data["site"])
                )
            elif event.type == "same-as:update":
                self.handle_same_as_update(
                    event,
                    event.data["user_uri"],
                    event.data["groups"],
                    event.data["diff_groups"],
                )
            elif event.type == "sample:add":
                self.handle_sample_add(event, Sample.from_dict(event.data["sample"]))
            elif event.type == "sample:update":
                self.handle_sample_update(event, Sample.from_dict(event.data["sample"]))
            else:
                raise ValueError(f"Unknown event type: {event.type}")

        self.handle_end(events)

    def handle_begin(self, events: Sequence[EventLog]):
        pass

    def handle_end(self, events: Sequence[EventLog]):
        pass

    def handle_site_add(
        self,
        event: EventLog,
        site: MineralSiteAndInventory,
        same_site_ids: list[InternalID],
    ):
        raise NotImplementedError()

    def handle_site_update(self, event: EventLog, site: MineralSiteAndInventory):
        raise NotImplementedError()

    def handle_same_as_update(
        self,
        event: EventLog,
        user_uri: str,
        groups: list[list[InternalID]],
        diff_groups: dict[InternalID, list[InternalID]],
    ):
        raise NotImplementedError()

    def handle_sample_add(self, event: EventLog, sample: Sample):
        raise NotImplementedError()

    def handle_sample_update(self, event: EventLog, sample: Sample):
        raise NotImplementedError()
