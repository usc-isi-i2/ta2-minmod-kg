"""Wipe the GeoChem dev stack and reload it from a new extraction drop.

python -m minmodkg.etl.geochem_refresh <drop_dir> --env-file deploy/geochem-dev/.env

Runs on the docker host. In order: check the drop, stop the services, back up
Postgres, Fuseki, the JSON-LD dir and the HMI files, wipe the GeoChem tables
and the triple store, load the drop into MinMod with the GeoChem loader,
upload every paper (JSON-LD, PDF, workbook, supplements) to a no-auth HMI,
restart the services and print what was loaded and what was left out.
User and entity tables are kept. Papers sharing a DOI or paper id are left
out, as are any passed with --exclude.
"""

from __future__ import annotations

import gzip
import json
import shutil
import subprocess
import tarfile
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

import httpx
import typer
from minmodkg.etl.geochem_drop import Drop, PaperBundle, scan

QUERIES = Path(__file__).parent / "queries"
COMPOSE_FILE = Path(__file__).parents[2] / "deploy/geochem-dev/compose.yml"
SERVICES = ["geochem-api", "minmod-sync", "geochem-sync", "hmi"]
HMI_LOAD_URL = "http://127.0.0.1:8399"
FUSEKI = "http://localhost:3030"

app = typer.Typer(pretty_exceptions_short=True, pretty_exceptions_enable=False)


def read_env(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


class Stack:
    def __init__(self, env_file: Path):
        self.env_file = env_file
        self.env = read_env(env_file)
        self.pg = self.env.get("POSTGRES_CONTAINER", "minmod-kg-postgres-1")
        self.fuseki = self.env.get("FUSEKI_CONTAINER", "minmod-kg")
        self.jsonld_dir = Path(self.env["GEOCHEM_JSONLD_DIR"])
        self.entity_dir = Path(self.env["ENTITY_DIR"])
        self.hmi_data_dir = Path(self.env["HMI_DATA_DIR"])

    def compose(self, *args: str) -> None:
        cmd = ["docker", "compose", "--env-file", str(self.env_file)]
        subprocess.run([*cmd, "-f", str(COMPOSE_FILE), *args], check=True)

    def psql(self, db: str, sql: str) -> list[list[str]]:
        out = subprocess.run(
            ["docker", "exec", "-i", self.pg, "psql", "-U", "minmod", "-d", db]
            + ["-v", "ON_ERROR_STOP=1", "-At", "-F", "\t"],
            input=sql,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return [row.split("\t") for row in out.splitlines() if row]

    def pg_dump(self, db: str, target: Path) -> None:
        with target.open("wb") as f:
            subprocess.run(
                ["docker", "exec", self.pg, "pg_dump", "-U", "minmod", "-Fc", db],
                stdout=f,
                check=True,
            )

    def fuseki_curl(self, *args: str) -> str:
        return subprocess.run(
            ["docker", "exec", self.fuseki, "curl", "-sf", *args],
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    def fuseki_dump(self, target: Path) -> None:
        cmd = ["docker", "exec", self.fuseki, "curl", "-sf"]
        cmd += ["-H", "Accept: application/n-quads", f"{FUSEKI}/minmod"]
        with subprocess.Popen(cmd, stdout=subprocess.PIPE) as proc:
            assert proc.stdout is not None
            with gzip.open(target, "wb") as f:
                shutil.copyfileobj(proc.stdout, f)
        if proc.returncode:
            raise RuntimeError(f"dumping Fuseki failed with {proc.returncode}")

    def fuseki_wipe(self) -> None:
        self.fuseki_curl(
            "-X",
            "POST",
            "--data-urlencode",
            "update=DROP ALL",
            f"{FUSEKI}/minmod/update",
        )

    def fuseki_compact(self) -> None:
        """Compact and wait for it; TDB2 never shrinks otherwise."""
        task = json.loads(
            self.fuseki_curl("-X", "POST", f"{FUSEKI}/$/compact/minmod?deleteOld=true")
        )
        while True:
            status = json.loads(self.fuseki_curl(f"{FUSEKI}/$/tasks/{task['taskId']}"))
            if "finished" in status:
                if not status.get("success", True):
                    raise RuntimeError(f"Fuseki compaction failed: {status}")
                return
            time.sleep(2)

    def triple_count(self) -> int:
        out = self.fuseki_curl(
            "-H",
            "Accept: text/csv",
            "--data-urlencode",
            "query=" + (QUERIES / "count_triples.rq").read_text(),
            f"{FUSEKI}/minmod/sparql",
        )
        return int(out.splitlines()[1])


def print_drop(drop: Drop) -> None:
    with_pdf = [b for b in drop.bundles if b.pdf]
    typer.echo(f"{len(drop.bundles)} papers to load, {len(with_pdf)} with a PDF")
    for paper_id, reason in drop.excluded.items():
        typer.secho(f"  excluded {paper_id}: {reason}", fg="yellow")
    if drop.without_doi:
        typer.echo(f"  no DOI, HMI only: {', '.join(drop.without_doi)}")
    for b in drop.bundles:
        if not b.pdf:
            typer.echo(f"  no PDF for {b.paper_id} ({b.pdf_status})")
        elif b.pdf_status == "no-text":
            typer.echo(f"  PDF for {b.paper_id} has no text to check: {b.pdf}")
    missing = [b for b in drop.bundles if b.missing_supplements]
    for b in missing:
        typer.echo(
            f"  {b.paper_id}: {len(b.missing_supplements)} cited supplements "
            f"not in the drop, e.g. {b.missing_supplements[0]}"
        )


def back_up(stack: Stack, backup_dir: Path) -> None:
    typer.echo(f"Backing up to {backup_dir}")
    backup_dir.mkdir(parents=True)
    stack.pg_dump("minmod", backup_dir / "minmod.dump")
    stack.pg_dump("geochem", backup_dir / "geochem.dump")
    stack.fuseki_dump(backup_dir / "fuseki.nq.gz")
    papers = stack.hmi_data_dir / "papers"
    if papers.is_dir():
        with tarfile.open(backup_dir / "hmi-data-papers.tgz", "w:gz") as tar:
            tar.add(papers, arcname="papers")


def stage_jsonld(stack: Stack, drop: Drop, backup_dir: Path) -> None:
    """Swap the JSON-LD dir for the drop's papers; the old one goes to the backup."""
    incoming = stack.jsonld_dir.with_name(stack.jsonld_dir.name + ".incoming")
    if incoming.exists():
        shutil.rmtree(incoming)
    incoming.mkdir()
    for b in drop.bundles:
        shutil.copy2(b.jsonld, incoming / b.jsonld.name)
    if stack.jsonld_dir.exists():
        shutil.move(stack.jsonld_dir, backup_dir / "geochem-jsonld")
    incoming.rename(stack.jsonld_dir)


def wait_for(url: str, timeout: float = 180) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=5).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise RuntimeError(f"{url} did not come up in {timeout}s")


def upload(client: httpx.Client, b: PaperBundle) -> Optional[str]:
    """Upload one paper the way the HMI dropzone does; returns an error."""
    paths = [("geochem_json", b.jsonld, "application/json")]
    if b.pdf:
        paths.append(("pdf", b.pdf, "application/pdf"))
    if b.workbook:
        paths.append(("xlsx", b.workbook, "application/octet-stream"))
    paths += [("spreadsheets", s, "application/octet-stream") for s in b.supplements]
    handles = [path.open("rb") for _, path, _ in paths]
    try:
        files = [
            (name, (path.name, fh, mime))
            for (name, path, mime), fh in zip(paths, handles)
        ]
        r = client.post(
            "/api/ingest/upload", data={"paper_id": b.paper_id}, files=files
        )
    finally:
        for fh in handles:
            fh.close()
    return None if r.status_code == 200 else f"{r.status_code} {r.text[:300]}"


def load_hmi(stack: Stack, drop: Drop) -> dict[str, str]:
    stack.compose("--profile", "refresh", "up", "-d", "--build", "hmi-load")
    try:
        wait_for(f"{HMI_LOAD_URL}/api/papers")
        client = httpx.Client(base_url=HMI_LOAD_URL, timeout=1800)
        old = client.get("/api/papers").raise_for_status().json()
        for p in old:
            client.delete(f"/api/papers/{p['paper_id']}").raise_for_status()
        typer.echo(f"Deleted {len(old)} HMI papers")
        failed = {}
        for i, b in enumerate(drop.bundles, 1):
            if error := upload(client, b):
                failed[b.paper_id] = error
            typer.echo(f"  [{i}/{len(drop.bundles)}] {b.paper_id} {error or 'ok'}")
        return failed
    finally:
        stack.compose("--profile", "refresh", "rm", "-sf", "hmi-load")


def counts(stack: Stack) -> dict[str, dict[str, int]]:
    def one(db: str, sql_file: str, names: list[str]) -> dict[str, int]:
        row = stack.psql(db, (QUERIES / sql_file).read_text())[0]
        return dict(zip(names, map(int, row)))

    return {
        "minmod": one("minmod", "geochem_counts.sql", ["papers", "sites", "samples"])
        | {"triples": stack.triple_count()},
        "hmi": one(
            "geochem", "hmi_counts.sql", ["papers", "deposits", "samples", "pdfs"]
        ),
    }


@app.command()
def main(
    drop_dir: Annotated[Path, typer.Argument(help="New extraction drop")],
    env_file: Annotated[
        Path, typer.Option(help="The dev stack's .env")
    ] = COMPOSE_FILE.parent / ".env",
    exclude: Annotated[
        Optional[list[str]], typer.Option(help="Paper ids to leave out")
    ] = None,
    backup_root: Annotated[
        Path, typer.Option(help="Each run backs up into a dated dir here")
    ] = Path("~/geochem-refresh-backups").expanduser(),
    check_only: Annotated[
        bool, typer.Option(help="Only report what the drop holds")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Don't ask to confirm")] = False,
):
    typer.echo(f"Checking {drop_dir}")
    drop = scan(drop_dir, frozenset(exclude or []))
    print_drop(drop)
    if check_only:
        return
    if not drop.bundles:
        raise typer.BadParameter(f"no papers to load in {drop_dir}")

    stack = Stack(env_file)
    foreign = int(
        stack.psql("minmod", (QUERIES / "geochem_foreign_sites.sql").read_text())[0][0]
    )
    if foreign:
        typer.secho(
            f"{foreign} sites in Postgres are not GeoChem's; refusing to wipe a "
            "database that is not GeoChem-only",
            fg="red",
        )
        raise typer.Exit(1)
    if not yes:
        typer.confirm(
            "Wipe the GeoChem data in Postgres, Fuseki and the HMI and reload it?",
            abort=True,
        )

    backup_dir = backup_root / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    stack.compose("stop", *SERVICES)
    back_up(stack, backup_dir)

    typer.echo("Wiping")
    stack.psql("minmod", (QUERIES / "geochem_wipe.sql").read_text())
    stack.fuseki_wipe()
    stack.fuseki_compact()
    stage_jsonld(stack, drop, backup_dir)

    typer.echo("Loading MinMod")
    stack.compose(
        "run",
        "--rm",
        "--no-deps",
        "geochem-api",
        "python",
        "-m",
        "minmodkg.etl.geochem_loader",
        str(stack.jsonld_dir),
        "--entity-dir",
        str(stack.entity_dir),
    )
    stack.fuseki_compact()

    typer.echo("Loading the HMI")
    failed = load_hmi(stack, drop)

    stack.compose("up", "-d", *SERVICES)
    result = counts(stack)
    report = {
        "drop": str(drop_dir),
        "backup": str(backup_dir),
        "excluded": drop.excluded,
        "without_doi": drop.without_doi,
        "hmi_upload_failed": failed,
        "counts": result,
        "papers": [
            {k: str(v) if isinstance(v, Path) else v for k, v in asdict(b).items()}
            | {"supplements": [str(s) for s in b.supplements]}
            for b in drop.bundles
        ],
    }
    (backup_dir / "report.json").write_text(json.dumps(report, indent=2))

    typer.echo("")
    print_drop(drop)
    for paper_id, error in failed.items():
        typer.secho(f"  HMI upload failed for {paper_id}: {error}", fg="red")
    m, h = result["minmod"], result["hmi"]
    typer.echo(
        f"MinMod: {m['papers']} papers, {m['sites']} sites, {m['samples']} samples, "
        f"{m['triples']} triples"
    )
    typer.echo(
        f"HMI: {h['papers']} papers, {h['deposits']} deposits, {h['samples']} samples, "
        f"{h['pdfs']} PDFs"
    )
    typer.echo(f"Report and backups: {backup_dir}")


if __name__ == "__main__":
    app()
