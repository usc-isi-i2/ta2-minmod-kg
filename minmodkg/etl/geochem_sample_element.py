"""Rebuild sample_element from every GeoChem sample.

Sample writes keep the table current; run this once after creating it, or
whenever its rules change. Safe to rerun and to run while the API is up.
"""

from __future__ import annotations

from typing import Annotated

import typer
from minmodkg.models.kgrel.base import create_db_and_tables, engine
from minmodkg.models.kgrel.sample import Sample
from minmodkg.models.kgrel.sample_element import replace_sample_elements
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session
from tqdm import tqdm

app = typer.Typer(pretty_exceptions_short=True, pretty_exceptions_enable=False)


def rebuild(engine: Engine, batch_size: int = 1000) -> int:
    """Rewrite every sample's rows; returns the number of samples."""
    with Session(engine) as session:
        total = session.execute(select(func.count()).select_from(Sample)).scalar_one()
    done = 0
    last_id = 0
    with tqdm(total=total, desc="samples") as progress:
        while True:
            with Session(engine, expire_on_commit=False) as session:
                # locked, so an edit can't land between reading and rewriting
                samples = list(
                    session.execute(
                        select(Sample)
                        .where(Sample.id > last_id)
                        .order_by(Sample.id)
                        .limit(batch_size)
                        .with_for_update()
                    ).scalars()
                )
                if not samples:
                    return done
                replace_sample_elements(session, samples)
                session.commit()
            last_id = samples[-1].id
            done += len(samples)
            progress.update(len(samples))


@app.command()
def main(
    batch_size: Annotated[int, typer.Option(help="Samples per transaction")] = 1000,
):
    create_db_and_tables()
    typer.echo(f"Rebuilt sample_element for {rebuild(engine, batch_size)} samples")


if __name__ == "__main__":
    app()
