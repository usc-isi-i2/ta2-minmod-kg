"""Find the papers in a GeoChem extraction drop and the files that go with them.

A drop is a directory tree holding `geochem_<paper_id>.canonical.jsonld`
files plus, anywhere below it, the papers' PDFs, the normalized
`extraction_<paper_id>.xlsx` workbooks and the supplementary files the
JSON-LD cites as `supp:<filename>`. A cited file may sit inside a .zip/.7z,
cited as `<archive stem>_<member path with / as _>`. Nothing here touches a
database, so a drop can be checked before anything is wiped.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

CANONICAL_SUFFIX = ".canonical.jsonld"
SUPPLEMENT_SUFFIXES = (".xlsx", ".xls", ".csv", ".tsv", ".docx", ".doc")
PDF_PAGES_CHECKED = 3


@dataclass
class PaperBundle:
    paper_id: str
    jsonld: Path
    doi: Optional[str]
    title: Optional[str]
    pdf: Optional[Path] = None
    pdf_status: str = "missing"
    workbook: Optional[Path] = None
    supplements: list[Path] = field(default_factory=list)
    # (archive, member, cited name) for cited files still inside an archive
    archived: list[tuple[Path, str, str]] = field(default_factory=list)
    missing_supplements: list[str] = field(default_factory=list)


@dataclass
class Drop:
    bundles: list[PaperBundle]
    excluded: dict[str, str]

    @property
    def without_doi(self) -> list[str]:
        return [b.paper_id for b in self.bundles if not b.doi]


def clean(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        value = value.get("value", value.get("@value"))
    if isinstance(value, str):
        return value.strip() or None
    return None


def paper_id_of(path: Path) -> str:
    return path.name.removeprefix("geochem_").removesuffix(CANONICAL_SUFFIX)


def doi_key(doi: str) -> str:
    return doi.replace("/", "_")


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def cited_supplements(doc: Any) -> list[str]:
    """Supplementary filenames the document cites, in first-seen order."""
    out: dict[str, None] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ("source", "artifact_path") and isinstance(child, str):
                    name = child.removeprefix("supp:").split("/")[-1].strip()
                    if name.lower().endswith(SUPPLEMENT_SUFFIXES):
                        out[name] = None
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(doc)
    return list(out)


def pdf_text(pdf: Path) -> str:
    from pypdf import PdfReader

    # font warnings drown the drop report and don't affect the text check
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    reader = PdfReader(pdf)
    return " ".join(
        page.extract_text() or "" for page in reader.pages[:PDF_PAGES_CHECKED]
    )


class PdfTexts:
    """Normalized first-page text per PDF, read once."""

    def __init__(self):
        self.texts: dict[Path, Optional[str]] = {}

    def get(self, pdf: Path) -> Optional[str]:
        if pdf not in self.texts:
            try:
                self.texts[pdf] = normalize(pdf_text(pdf))
            except Exception:
                self.texts[pdf] = None
        return self.texts[pdf]

    def check(self, pdf: Path, doi: Optional[str], title: Optional[str]) -> str:
        """`doi`/`title` when the first pages show it, `no-text` for a scan,
        `unreadable`, or `mismatch` when they show neither."""
        text = self.get(pdf)
        if text is None:
            return "unreadable"
        if not text:
            return "no-text"
        if doi and normalize(doi) in text:
            return "doi"
        if title and normalize(title)[:60] in text:
            return "title"
        return "mismatch"


ACCEPTED = ("doi", "title", "no-text")


def pick(candidates: list[Path], hints: list[str]) -> Optional[Path]:
    """The candidate whose path mentions one of the hints, else the first."""
    for path in candidates:
        if any(hint in path.parts for hint in hints):
            return path
    return candidates[0] if candidates else None


def archive_members(archive: Path) -> list[str]:
    if archive.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(archive) as zf:
                return [n for n in zf.namelist() if not n.endswith("/")]
        except (zipfile.BadZipFile, OSError):
            return []
    # .7z needs bsdtar (libarchive) or 7z on the host
    if shutil.which("bsdtar"):
        cmd = ["bsdtar", "-tf", str(archive)]
    else:
        cmd = ["7z", "l", "-ba", "-slt", str(archive)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=False).stdout
    except OSError:
        return []
    if cmd[0] == "7z":
        lines = [
            ln.split("= ", 1)[1] for ln in out.splitlines() if ln.startswith("Path = ")
        ]
    else:
        lines = out.splitlines()
    return [n for n in lines if n and not n.endswith("/")]


def archived_names(archives: list[Path]) -> dict[str, tuple[Path, str]]:
    """Cited name -> (archive, member) for every member of every archive."""
    out = {}
    for archive in archives:
        for member in archive_members(archive):
            if member.startswith("__MACOSX/") or Path(member).name.startswith("._"):
                continue
            out.setdefault(
                f"{archive.stem}_{member.replace('/', '_')}", (archive, member)
            )
    return out


def extract(archive: Path, member: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as out:
        if archive.suffix.lower() == ".zip":
            with zipfile.ZipFile(archive) as zf, zf.open(member) as src:
                shutil.copyfileobj(src, out)
        else:
            if shutil.which("bsdtar"):
                cmd = ["bsdtar", "-xOf", str(archive), member]
            else:
                cmd = ["7z", "x", "-so", str(archive), member]
            subprocess.run(cmd, stdout=out, check=True)


def scan(drop_dir: Path, exclude: frozenset[str] = frozenset()) -> Drop:
    files = [p for p in drop_dir.rglob("*") if p.is_file()]
    by_name: dict[str, list[Path]] = defaultdict(list)
    for path in sorted(files):
        by_name[path.name].append(path)

    docs: dict[str, tuple[Path, dict]] = {}
    excluded: dict[str, str] = {}
    for path in sorted(p for p in files if p.name.endswith(CANONICAL_SUFFIX)):
        paper_id = paper_id_of(path)
        if paper_id in docs:
            excluded[paper_id] = f"paper id is in two files, also {path}"
            continue
        docs[paper_id] = (path, json.loads(path.read_text(encoding="utf-8")))

    owners: dict[str, list[str]] = defaultdict(list)
    for paper_id, (_, doc) in docs.items():
        if doi := clean(doc.get("paper_doi")):
            owners[doi].append(paper_id)
    for doi, paper_ids in owners.items():
        if len(paper_ids) > 1:
            for paper_id in paper_ids:
                others = ", ".join(p for p in paper_ids if p != paper_id)
                excluded[paper_id] = f"DOI {doi} is shared with {others}"
    for paper_id in exclude & docs.keys():
        excluded.setdefault(paper_id, "excluded on the command line")

    archives = [p for p in files if p.suffix.lower() in (".zip", ".7z")]
    in_archives = archived_names(archives) if archives else {}
    texts = PdfTexts()
    bundles = []
    for paper_id, (path, doc) in docs.items():
        if paper_id in excluded:
            continue
        doi = clean(doc.get("paper_doi"))
        bundle = PaperBundle(
            paper_id=paper_id,
            jsonld=path,
            doi=doi,
            title=clean(doc.get("paper_title")) or clean(doc.get("title")),
        )
        hints = [paper_id] + ([doi_key(doi)] if doi else [])
        pdf_names = [f"{paper_id}.pdf"] + ([f"{doi_key(doi)}.pdf"] if doi else [])
        pdfs = [p for name in pdf_names for p in by_name.get(name, [])]
        if doi:
            pdfs += [p for p in files if p.suffix == ".pdf" and doi_key(doi) in p.parts]
        for pdf in dict.fromkeys(pdfs):
            status = texts.check(pdf, doi, bundle.title)
            if status in ACCEPTED:
                bundle.pdf, bundle.pdf_status = pdf, status
                break
            bundle.pdf_status = f"{status}: {pdf}"
        bundle.workbook = pick(by_name.get(f"extraction_{paper_id}.xlsx", []), hints)
        for name in cited_supplements(doc):
            if found := pick(by_name.get(name, []), hints):
                bundle.supplements.append(found)
            elif name in in_archives:
                bundle.archived.append((*in_archives[name], name))
            else:
                bundle.missing_supplements.append(name)
        bundles.append(bundle)
    match_by_content(bundles, [p for p in files if p.suffix == ".pdf"], texts)
    return Drop(bundles=bundles, excluded=dict(sorted(excluded.items())))


def match_by_content(
    bundles: list[PaperBundle], pdfs: list[Path], texts: PdfTexts
) -> None:
    """Give a paper still without a PDF the unclaimed PDF that shows its DOI,
    for PDFs saved under another name."""
    claimed = {b.pdf for b in bundles if b.pdf}
    for b in bundles:
        if b.pdf or not b.doi:
            continue
        for pdf in pdfs:
            if pdf not in claimed and (text := texts.get(pdf)):
                if normalize(b.doi) in text:
                    b.pdf, b.pdf_status = pdf, "doi, other name"
                    claimed.add(pdf)
                    break
