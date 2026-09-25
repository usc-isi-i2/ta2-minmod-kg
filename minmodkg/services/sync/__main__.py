from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated, Optional

import typer
from loguru import logger
from minmodkg.services.sync.backup_listener import BackupListener
from minmodkg.services.sync.kgsync_listener import KGSyncListener
from minmodkg.services.sync.sync import process_pending_events

app = typer.Typer(pretty_exceptions_short=True, pretty_exceptions_enable=False)


@app.command()
def main(
    repo_dir: Path,
    jsonld_dir: Annotated[
        Optional[Path],
        typer.Option(help="GeoChem JSON-LD directory that paper edits are written to"),
    ] = None,
    backup_interval: int = 3600,
    batch_size: int = 500,
    verbose: Annotated[bool, typer.Option("--verbose")] = False,
):
    """Synchronize data from the KGRel to KG and CDR."""
    kgsync_listener = KGSyncListener()
    backup_listener = BackupListener(repo_dir, jsonld_dir)

    last_backup_synced: Optional[int] = None

    while True:
        try:
            # we want kg sync to be near real-time
            process_pending_events(kgsync_listener, batch_size, verbose=verbose)
        except Exception as e:
            # exception occurred, we will wait for X second before trying again
            logger.exception(e)
            logger.info("Wait for 10 seconds before trying again")
            time.sleep(10)

        try:
            if backup_interval > 0:
                # only run the backup sync if the interval is greater than 0
                # record the current hour
                current_hour = int(time.time() / backup_interval)
                if last_backup_synced is None or current_hour > last_backup_synced:
                    process_pending_events(backup_listener, batch_size, verbose=verbose)
                    last_backup_synced = current_hour
        except Exception as e:
            # exception occurred, we will wait for X second before trying again
            logger.exception(e)
            logger.info("Wait for 10 seconds before trying again")
            time.sleep(10)

        # wait for a second before checking again
        time.sleep(1)

        if verbose:
            print(".", end="", flush=True)


if __name__ == "__main__":
    app()
