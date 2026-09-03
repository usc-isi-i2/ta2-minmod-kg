from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from minmodkg.misc.utils import datetime_to_nanoseconds, format_nanoseconds, makedict
from minmodkg.models.kg.reference import Reference
from minmodkg.models.kg.sample import Analysis, EditEvent
from minmodkg.models.kg.sample import Sample as KGSample
from minmodkg.models.kgrel.base import Base
from minmodkg.typing import InternalID
from sqlalchemy import BigInteger, ForeignKey
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column

if TYPE_CHECKING:
    pass


class Sample(MappedAsDataclass, Base):
    __tablename__ = "sample"

    id: Mapped[int] = mapped_column(primary_key=True, init=False)

    # The computed URI-slug (make_sample_id(mineral_site_id, sample_id)), analogous to
    # MineralSite.site_id -- used for lookup/URIs. Deliberately NOT named sample_id:
    # the ontology already has its own :sample_id property (raw source data, e.g.
    # "DH-23-045"), stored separately below. MineralSite doesn't have this collision
    # since there's no mo:site_id property to clash with.
    public_id: Mapped[InternalID] = mapped_column(unique=True)

    # string FK against mineral_site.site_id (not the int PK) -- Sample's own uri is
    # computed from the parent site's *string* site_id (see make_sample_id), so this
    # avoids a join back to mineral_site just to build/verify that uri.
    mineral_site_id: Mapped[InternalID] = mapped_column(
        ForeignKey("mineral_site.site_id", ondelete="CASCADE"), index=True
    )

    # raw :sample_id ontology property value -- NOT guaranteed unique on its own (see
    # make_sample_id's docstring in transformations.py)
    sample_id: Mapped[str | None] = mapped_column()

    sample_name: Mapped[str | None] = mapped_column()
    sample_local_id: Mapped[str | None] = mapped_column()
    sample_type: Mapped[str | None] = mapped_column()
    collection_date: Mapped[str | None] = mapped_column()
    description: Mapped[str | None] = mapped_column()
    mineral: Mapped[str | None] = mapped_column()
    sampling_method: Mapped[str | None] = mapped_column()
    sample_preparation: Mapped[str | None] = mapped_column()
    material_class: Mapped[str | None] = mapped_column()
    material_class_comment: Mapped[str | None] = mapped_column()
    analysed_material: Mapped[str | None] = mapped_column()
    sample_deposit_relation: Mapped[str | None] = mapped_column()
    geological_province: Mapped[str | None] = mapped_column()
    strat_unit_uid: Mapped[str | None] = mapped_column()
    strat_grouping: Mapped[str | None] = mapped_column()
    earth_material_group: Mapped[str | None] = mapped_column()
    earth_material_qualifier: Mapped[str | None] = mapped_column()
    mode_occurrence: Mapped[str | None] = mapped_column()
    metamorphic_grade: Mapped[str | None] = mapped_column()
    alteration: Mapped[str | None] = mapped_column()
    paragenetic_stage: Mapped[str | None] = mapped_column()
    texture: Mapped[str | None] = mapped_column()
    color: Mapped[str | None] = mapped_column()
    associated_minerals: Mapped[str | None] = mapped_column()
    feature_type: Mapped[str | None] = mapped_column()
    feature_name: Mapped[str | None] = mapped_column()
    feature_local_uid: Mapped[str | None] = mapped_column()
    top_depth_m: Mapped[float | None] = mapped_column()
    bottom_depth_m: Mapped[float | None] = mapped_column()
    comments: Mapped[str | None] = mapped_column()

    analyses: Mapped[list[Analysis]] = mapped_column()
    reference: Mapped[list[Reference]] = mapped_column()
    # additive-only: appended to on every write, never overwritten (see EditEvent)
    edit_history: Mapped[list[EditEvent]] = mapped_column()

    # timestamp in nanoseconds, same convention as MineralSite.modified_at -- backs
    # the snapshot_id optimistic-concurrency check on updates
    modified_at: Mapped[int] = mapped_column(BigInteger)

    def set_id(self, id: int) -> Sample:
        self.id = id
        return self

    def to_kg(self) -> KGSample:
        return KGSample(
            sample_id=self.sample_id,
            mineral_site_id=self.mineral_site_id,
            sample_name=self.sample_name,
            sample_local_id=self.sample_local_id,
            sample_type=self.sample_type,
            collection_date=self.collection_date,
            description=self.description,
            mineral=self.mineral,
            sampling_method=self.sampling_method,
            sample_preparation=self.sample_preparation,
            material_class=self.material_class,
            material_class_comment=self.material_class_comment,
            analysed_material=self.analysed_material,
            sample_deposit_relation=self.sample_deposit_relation,
            geological_province=self.geological_province,
            strat_unit_uid=self.strat_unit_uid,
            strat_grouping=self.strat_grouping,
            earth_material_group=self.earth_material_group,
            earth_material_qualifier=self.earth_material_qualifier,
            mode_occurrence=self.mode_occurrence,
            metamorphic_grade=self.metamorphic_grade,
            alteration=self.alteration,
            paragenetic_stage=self.paragenetic_stage,
            texture=self.texture,
            color=self.color,
            associated_minerals=self.associated_minerals,
            feature_type=self.feature_type,
            feature_name=self.feature_name,
            feature_local_uid=self.feature_local_uid,
            top_depth_m=self.top_depth_m,
            bottom_depth_m=self.bottom_depth_m,
            comments=self.comments,
            analyses=self.analyses,
            reference=self.reference,
            edit_history=self.edit_history,
        )

    @staticmethod
    def from_raw_sample(raw_sample: dict | KGSample) -> Sample:
        sample = (
            KGSample.from_dict(raw_sample)
            if isinstance(raw_sample, dict)
            else raw_sample
        )
        return Sample(
            public_id=sample.id,
            mineral_site_id=sample.mineral_site_id,
            sample_id=sample.sample_id,
            sample_name=sample.sample_name,
            sample_local_id=sample.sample_local_id,
            sample_type=sample.sample_type,
            collection_date=sample.collection_date,
            description=sample.description,
            mineral=sample.mineral,
            sampling_method=sample.sampling_method,
            sample_preparation=sample.sample_preparation,
            material_class=sample.material_class,
            material_class_comment=sample.material_class_comment,
            analysed_material=sample.analysed_material,
            sample_deposit_relation=sample.sample_deposit_relation,
            geological_province=sample.geological_province,
            strat_unit_uid=sample.strat_unit_uid,
            strat_grouping=sample.strat_grouping,
            earth_material_group=sample.earth_material_group,
            earth_material_qualifier=sample.earth_material_qualifier,
            mode_occurrence=sample.mode_occurrence,
            metamorphic_grade=sample.metamorphic_grade,
            alteration=sample.alteration,
            paragenetic_stage=sample.paragenetic_stage,
            texture=sample.texture,
            color=sample.color,
            associated_minerals=sample.associated_minerals,
            feature_type=sample.feature_type,
            feature_name=sample.feature_name,
            feature_local_uid=sample.feature_local_uid,
            top_depth_m=sample.top_depth_m,
            bottom_depth_m=sample.bottom_depth_m,
            comments=sample.comments,
            analyses=sample.analyses,
            reference=sample.reference,
            edit_history=sample.edit_history,
            modified_at=datetime_to_nanoseconds(datetime.utcnow()),
        )

    def to_dict(self):
        return makedict.without_none_or_empty_list(
            (
                ("id", self.id),
                ("public_id", self.public_id),
                ("mineral_site_id", self.mineral_site_id),
                ("sample_id", self.sample_id),
                ("sample_name", self.sample_name),
                ("sample_local_id", self.sample_local_id),
                ("sample_type", self.sample_type),
                ("collection_date", self.collection_date),
                ("description", self.description),
                ("mineral", self.mineral),
                ("sampling_method", self.sampling_method),
                ("sample_preparation", self.sample_preparation),
                ("material_class", self.material_class),
                ("material_class_comment", self.material_class_comment),
                ("analysed_material", self.analysed_material),
                ("sample_deposit_relation", self.sample_deposit_relation),
                ("geological_province", self.geological_province),
                ("strat_unit_uid", self.strat_unit_uid),
                ("strat_grouping", self.strat_grouping),
                ("earth_material_group", self.earth_material_group),
                ("earth_material_qualifier", self.earth_material_qualifier),
                ("mode_occurrence", self.mode_occurrence),
                ("metamorphic_grade", self.metamorphic_grade),
                ("alteration", self.alteration),
                ("paragenetic_stage", self.paragenetic_stage),
                ("texture", self.texture),
                ("color", self.color),
                ("associated_minerals", self.associated_minerals),
                ("feature_type", self.feature_type),
                ("feature_name", self.feature_name),
                ("feature_local_uid", self.feature_local_uid),
                ("top_depth_m", self.top_depth_m),
                ("bottom_depth_m", self.bottom_depth_m),
                ("comments", self.comments),
                ("analyses", [a.to_dict() for a in self.analyses]),
                ("reference", [r.to_dict() for r in self.reference]),
                ("edit_history", [e.to_dict() for e in self.edit_history]),
                ("modified_at", self.modified_at),
            )
        )

    @classmethod
    def from_dict(cls, d: dict) -> Sample:
        obj = cls(
            public_id=d["public_id"],
            mineral_site_id=d["mineral_site_id"],
            sample_id=d.get("sample_id"),
            sample_name=d.get("sample_name"),
            sample_local_id=d.get("sample_local_id"),
            sample_type=d.get("sample_type"),
            collection_date=d.get("collection_date"),
            description=d.get("description"),
            mineral=d.get("mineral"),
            sampling_method=d.get("sampling_method"),
            sample_preparation=d.get("sample_preparation"),
            material_class=d.get("material_class"),
            material_class_comment=d.get("material_class_comment"),
            analysed_material=d.get("analysed_material"),
            sample_deposit_relation=d.get("sample_deposit_relation"),
            geological_province=d.get("geological_province"),
            strat_unit_uid=d.get("strat_unit_uid"),
            strat_grouping=d.get("strat_grouping"),
            earth_material_group=d.get("earth_material_group"),
            earth_material_qualifier=d.get("earth_material_qualifier"),
            mode_occurrence=d.get("mode_occurrence"),
            metamorphic_grade=d.get("metamorphic_grade"),
            alteration=d.get("alteration"),
            paragenetic_stage=d.get("paragenetic_stage"),
            texture=d.get("texture"),
            color=d.get("color"),
            associated_minerals=d.get("associated_minerals"),
            feature_type=d.get("feature_type"),
            feature_name=d.get("feature_name"),
            feature_local_uid=d.get("feature_local_uid"),
            top_depth_m=d.get("top_depth_m"),
            bottom_depth_m=d.get("bottom_depth_m"),
            comments=d.get("comments"),
            analyses=[Analysis.from_dict(a) for a in d.get("analyses", [])],
            reference=[Reference.from_dict(r) for r in d.get("reference", [])],
            edit_history=[EditEvent.from_dict(e) for e in d.get("edit_history", [])],
            modified_at=d["modified_at"],
        )
        if "id" in d:
            obj.id = d["id"]
        return obj
