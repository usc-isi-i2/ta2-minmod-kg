from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, NamedTuple, NotRequired, TypedDict

import orjson
import serde.json
import xxhash
from libactor.cache import cache
from minmodkg.misc.utils import group_by, makedict
from minmodkg.models.kg.base import MINMOD_KG
from minmodkg.models.kg.mineral_site import MineralSiteIdent
from minmodkg.models.kgrel.dedup_mineral_site import DedupMineralSite
from minmodkg.models.kgrel.mineral_site import MineralSite as RelMineralSite
from minmodkg.models.kgrel.mineral_site import MineralSiteAndInventory
from minmodkg.services.kgrel_entity import FileEntityService
from minmodkg.typing import InternalID
from slugify import slugify
from timer import Timer
from tqdm import tqdm

from statickg.helper import FileSqliteBackend, get_parallel_executor, typed_delayed
from statickg.models.etl import ETLOutput
from statickg.models.file_and_path import InputFile, RelPath
from statickg.models.repository import Repository
from statickg.services.interface import BaseFileService, BaseService

COMPRESSION: str = ".lz4"
# COMPRESSION: str = ""


class MineralSiteETLServiceConstructArgs(TypedDict):
    verbose: NotRequired[int]
    parallel: NotRequired[bool]


class MineralSiteETLServiceInvokeArgs(TypedDict):
    mineral_site_dir: RelPath
    entity_dir: RelPath

    same_as_group: RelPath

    output: RelPath

    optional: NotRequired[bool]
    compute_missing_file_key: NotRequired[bool]


@dataclass
class MineralSiteFileInfo:
    username: str
    source_name: str
    bucket_no: str

    @staticmethod
    def from_file(ms_infile: Path):
        return MineralSiteFileInfo(
            bucket_no=ms_infile.name.split(".")[0],
            source_name=ms_infile.parent.name,
            username=ms_infile.parent.parent.name,
        )


class SourceInfo(NamedTuple):
    source_name: str
    bucket_no: str

    @staticmethod
    def from_file(ms_infile: Path):
        return SourceInfo(
            bucket_no=ms_infile.name.split(".")[0],
            source_name=ms_infile.parent.name,
        )


class MineralSiteETLService(BaseFileService[MineralSiteETLServiceConstructArgs]):
    """Merge the mineral site data across multiple files"""

    def __init__(
        self,
        name: str,
        workdir: Path,
        args: MineralSiteETLServiceConstructArgs,
        services: Mapping[str, BaseService],
    ):
        super().__init__(name, workdir, args, services)
        self.verbose = args.get("verbose", 1)
        self.parallel = args.get("parallel", True)

    def forward(
        self, repo: Repository, args: MineralSiteETLServiceInvokeArgs, output: ETLOutput
    ):
        """Execute the merge process"""
        infiles = self.list_files(
            repo,
            args["mineral_site_dir"] / "*/*/*.json*",
            unique_filepath=True,
            optional=args.get("optional", False),
            compute_missing_file_key=args.get("compute_missing_file_key", True),
        )

        # we are going partition the data into (source_dir, bucket_dir) => file
        group_infiles = makedict.group_keys(
            (SourceInfo.from_file(infile.path), infile) for infile in infiles
        )

        timer = Timer()
        with timer.watch_and_report("Deduping the data"):
            dedup_files = self.dedup(args, group_infiles)
        with timer.watch_and_report("Merging the data"):
            merge_output = self.merge(args, group_infiles, dedup_files)
        with timer.watch_and_report("Preparing KG input"):
            self.prep_kg_input(args, merge_output)
        with timer.watch_and_report("Preparing KGRel input"):
            self.prep_kgrel_input(args, merge_output)

    def dedup(
        self,
        args: MineralSiteETLServiceInvokeArgs,
        grouped_infiles: dict[SourceInfo, list[InputFile]],
    ) -> dict[SourceInfo, InputFile]:
        def read_site_data(
            group: SourceInfo,
            infile: Path,
        ) -> tuple[SourceInfo, dict[InternalID, dict]]:
            """Read the site ids from the file"""
            lst = serde.json.deser(infile)
            output = {}
            for r in lst:
                site = MineralSiteIdent.from_dict(r)
                assert site.id not in output, (site.record_id, infile)
                output[site.id] = {
                    "site_id": site.id,
                    "source_id": site.source_id,
                    "record_id": site.record_id,
                    "extra": {
                        # needed to compute the dedup information
                        # "source_score": r["source_score"],
                        # "created_by": r.created_by,
                        # "modified_at": datetime.fromisoformat(r.modified_at),
                        # "name": r.name is not None,
                        # "rank": r.site_rank is not None,
                        # "type": r.site_type is not None,
                    },
                }
            return (group, output)

        same_as_outdir = args["output"].get_path() / "same_as"
        same_as_outdir.mkdir(parents=True, exist_ok=True)

        # read site data
        it: Iterable[tuple[SourceInfo, dict[InternalID, dict]]] = get_parallel_executor(
            self.parallel
        )(
            typed_delayed(read_site_data)(group, infile.path)
            for group, lst in grouped_infiles.items()
            for infile in lst
        )
        sites: dict[InternalID, dict] = {}
        group2ids: dict[SourceInfo, list[InternalID]] = defaultdict(list)
        for group, d in tqdm(it):
            n_sites = len(sites)
            sites.update(d)
            assert len(sites) == n_sites + len(
                d
            ), f"Encounter duplicate site ids in {group}"
            group2ids[group].extend(list(d.keys()))

        # read all the same as sites
        groups: list[list[InternalID]] = [
            filtered_lst
            for lst in serde.json.deser(args["same_as_group"].get_path())
            if len((filtered_lst := [id for id in lst if id in sites])) > 0
        ]
        linked_sites = {site for grp in groups for site in grp}

        # add sites that are not linked first
        for site_id, site in sites.items():
            if site_id not in linked_sites:
                groups.append([site_id])

        # assert no overlapping between groups
        seen_ids = set()
        for grp in groups:
            for site_id in grp:
                assert site_id not in seen_ids
                seen_ids.add(site_id)

        # assign dedup id to each site
        for grp in groups:
            dedup_id = RelMineralSite.get_dedup_id(grp)
            for site_id in grp:
                sites[site_id]["dedup_id"] = dedup_id

        # save the results
        output = {}
        output_files: set[Path] = set()
        for group in grouped_infiles.keys():
            outfile = (
                same_as_outdir
                / f"{group.source_name}/{group.bucket_no}.json{COMPRESSION}"
            )
            outfile.parent.mkdir(parents=True, exist_ok=True)
            serde.json.ser(
                [
                    {
                        "site_id": site_id,
                        "dedup_id": sites[site_id]["dedup_id"],
                    }
                    for site_id in sorted(group2ids[group])
                ],
                outfile,
            )

            output_files.add(outfile)
            output[group] = InputFile.from_relpath(
                RelPath(
                    args["output"].basetype,
                    args["output"].basepath,
                    str(outfile.relative_to(args["output"].basepath)),
                )
            )

        self.remove_unknown_files(output_files, same_as_outdir)
        return output

    def merge(
        self,
        args: MineralSiteETLServiceInvokeArgs,
        grouped_files: dict[SourceInfo, list[InputFile]],
        dedup_files: dict[SourceInfo, InputFile],
    ) -> dict[SourceInfo, InputFile]:
        outdir = args["output"].get_path()

        merged_outdir = outdir / "merged"
        merged_outdir.mkdir(parents=True, exist_ok=True)

        entity_dir = args["entity_dir"].get_path()

        it: Iterable[tuple[SourceInfo, Path]] = get_parallel_executor(self.parallel)(
            typed_delayed(MergeFn.exec)(
                self.workdir,
                entity_dir=entity_dir,
                group=group,
                infiles=infiles,
                sameas_file=dedup_files[group],
                outfile=merged_outdir
                / group.source_name
                / f"{group.bucket_no}.json{COMPRESSION}",
            )
            for group, infiles in grouped_files.items()
        )
        merged_outfiles: dict[SourceInfo, Path] = {}
        for group, file in tqdm(
            it,
            total=len(grouped_files),
            desc="Merging the data",
            disable=self.verbose < 1,
        ):
            merged_outfiles[group] = file
        self.remove_unknown_files(set(merged_outfiles.values()), merged_outdir)

        output: dict[SourceInfo, InputFile] = {}
        for group, outfile in merged_outfiles.items():
            output[group] = InputFile.from_relpath(
                RelPath(
                    basetype=args["output"].basetype,
                    basepath=args["output"].basepath,
                    relpath=str(outfile.relative_to(args["output"].basepath)),
                )
            )
        return output

    def prep_kg_input(
        self,
        args: MineralSiteETLServiceInvokeArgs,
        merge_files: dict[SourceInfo, InputFile],
    ):
        kg_outdir = args["output"].get_path() / "kg"
        kg_outdir.mkdir(parents=True, exist_ok=True)
        it: Iterable[Path] = get_parallel_executor(self.parallel)(
            typed_delayed(ExportTTLFn.exec)(
                self.workdir,
                infile=infile,
                outfile=kg_outdir / group.source_name / f"{group.bucket_no}.ttl",
            )
            for group, infile in merge_files.items()
        )

        kg_outfiles = set()
        for file in tqdm(
            it,
            total=len(merge_files),
            desc="Exporting TTL",
            disable=self.verbose < 1,
        ):
            kg_outfiles.add(file)
        self.remove_unknown_files(kg_outfiles, kg_outdir)

    def prep_kgrel_input(
        self,
        args: MineralSiteETLServiceInvokeArgs,
        merge_files: dict[SourceInfo, InputFile],
    ):
        kgrel_outdir = args["output"].get_path() / "kgrel"
        kgrel_outdir.mkdir(parents=True, exist_ok=True)

        # read the merged files
        dedup_sites: dict[InternalID, list[DedupMineralSite]] = defaultdict(list)
        id2site: dict[InternalID, MineralSiteAndInventory] = {}
        for infile in tqdm(
            merge_files.values(),
            total=len(merge_files),
            desc="Reading KGRel input",
            disable=self.verbose < 1,
        ):
            d = serde.json.deser(infile.path)
            for x in d["DedupMineralSite"]:
                dms = DedupMineralSite.from_dict(x)
                dedup_sites[dms.id].append(dms)
            for x in d["MineralSiteAndInventory"]:
                msi = MineralSiteAndInventory.from_dict(x)
                assert msi.ms.site_id not in id2site
                id2site[msi.ms.site_id] = msi

        output_dedup_sites = []
        output_dedup_inventories = []
        output_sites = []
        output_inventories = []
        for lst in tqdm(
            dedup_sites.values(),
            total=len(dedup_sites),
            desc="Creating Dedup Mineral Site",
            disable=self.verbose < 1,
        ):
            dedup_site = DedupMineralSite.from_dedup_sites(
                lst,
                [id2site[rms.site_id] for dms in lst for rms in dms.ranked_sites],
                is_site_ranked=True,
            )
            output_dedup_sites.append(dedup_site.dms.to_dict())
            output_dedup_inventories.extend([inv.to_dict() for inv in dedup_site.invs])

        for msi in tqdm(
            id2site.values(),
            total=len(id2site),
            desc="Creating Mineral Site",
            disable=self.verbose < 1,
        ):
            output_sites.append(msi.ms.to_dict())
            output_inventories.append(
                {"invs": [inv.to_dict() for inv in msi.invs], "site": msi.ms.site_id}
            )

        serde.json.ser(
            {
                "DedupMineralSite": output_dedup_sites,
                "MineralSite": output_sites,
                "MineralInventoryView": output_inventories,
                "DedupMineralInventoryView": output_dedup_inventories,
            },
            kgrel_outdir / ("dedup_sites.json" + COMPRESSION),
        )


class MergeFn:
    """Merge multiple files of the same source into a single file"""

    instances = {}

    def __init__(
        self,
        workdir: Path,
        entity_dir: Path,
    ):
        self.workdir = workdir
        self.entity_service = FileEntityService(entity_dir)

    @staticmethod
    def get_instance(
        workdir: Path,
        entity_dir: Path,
    ) -> MergeFn:
        if workdir not in MergeFn.instances:
            MergeFn.instances[workdir] = MergeFn(workdir, entity_dir)
        return MergeFn.instances[workdir]

    @classmethod
    def exec(
        cls,
        workdir: Path,
        entity_dir: Path,
        group: SourceInfo,
        **kwargs,
    ) -> tuple[SourceInfo, Path]:
        return group, cls.get_instance(workdir, entity_dir).invoke(**kwargs)

    @cache(
        backend=FileSqliteBackend.factory(filename="merge-v108.sqlite"),
        cache_ser_args={
            "infiles": lambda lst: orjson.dumps(
                sorted(x.get_ident() for x in lst)
            ).decode(),
            "sameas_file": lambda x: x.get_ident(),
        },
    )
    def invoke(
        self, infiles: list[InputFile], sameas_file: InputFile, outfile: Path
    ) -> Path:
        rid2sites = defaultdict(list)
        for infile in sorted(infiles, key=lambda x: x.path):
            for r in serde.json.deser(infile.path):
                rid2sites[slugify(r["record_id"])].append(r)

        dedup_map = {
            r["site_id"]: r["dedup_id"] for r in serde.json.deser(sameas_file.path)
        }

        outfile.parent.mkdir(parents=True, exist_ok=True)
        # merge the data
        lst_msi: list[MineralSiteAndInventory] = []
        for infile in sorted(infiles, key=lambda x: x.path):
            for raw_site in serde.json.deser(infile.path):
                norm_site = MineralSiteAndInventory.from_raw_site(
                    raw_site,
                    commodity_form_conversion=self.entity_service.get_commodity_form_conversion(),
                    crs_names=self.entity_service.get_crs_name(),
                    source_score=self.entity_service.get_data_source_score(),
                    state_index=self.entity_service.get_state_or_province_index(),
                )
                norm_site.ms.dedup_site_id = dedup_map[norm_site.ms.site_id]
                lst_msi.append(norm_site)

        lst_dms = [
            DedupMineralSite.from_sites(sites)
            for dedup_id, sites in group_by(
                lst_msi, lambda site: site.ms.dedup_site_id
            ).items()
        ]
        serde.json.ser(
            {
                "MineralSiteAndInventory": [msi.to_dict() for msi in lst_msi],
                "DedupMineralSite": [dms.dms.to_dict() for dms in lst_dms],
            },
            outfile,
        )
        return outfile


class ExportTTLFn:
    instances = {}

    def __init__(self, workdir: Path):
        self.workdir = workdir

    @staticmethod
    def get_instance(workdir: Path) -> ExportTTLFn:
        if workdir not in ExportTTLFn.instances:
            ExportTTLFn.instances[workdir] = ExportTTLFn(workdir)
        return ExportTTLFn.instances[workdir]

    @classmethod
    def exec(cls, workdir: Path, **kwargs) -> Path:
        return cls.get_instance(workdir).invoke(**kwargs)

    @cache(
        backend=FileSqliteBackend.factory(filename="export-v101.sqlite"),
        cache_ser_args={
            "infile": lambda x: x.get_ident(),
        },
    )
    def invoke(self, infile: InputFile, outfile: Path) -> Path:
        output = []
        for d in serde.json.deser(infile.path)["MineralSiteAndInventory"]:
            msi = MineralSiteAndInventory.from_dict(d)
            output.extend(msi.ms.to_kg().to_triples())

        outfile.parent.mkdir(parents=True, exist_ok=True)
        with open(outfile, "w") as f:
            f.write(MINMOD_KG.prefix_part)
            f.write("\n")
            for triple in output:
                f.write(f" ".join(triple))
                f.write(". \n")
        return outfile
