from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Body, HTTPException, Query, status
from minmodkg.api.dependencies import CurrentUserDep, SampleServiceDep
from minmodkg.api.models.public_sample import InputPublicSample, OutputPublicSample
from minmodkg.services.kgrel_entity import EntityService
from minmodkg.services.sample import (
    ArgumentError,
    ExpiredSnapshotIdError,
    SampleNotFoundError,
    SampleService,
)
from minmodkg.transformations import make_sample_id
from minmodkg.typing import InternalID
from minmodkg.validators import validate_sample as _validate_sample
from sqlalchemy.exc import IntegrityError

router = APIRouter(tags=["samples"])


@router.get("/samples/{sample_id}")
def get_sample(sample_id: InternalID, sample_service: SampleServiceDep):
    sample = sample_service.find_by_id(sample_id)
    if sample is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The requested sample does not exist.",
        )
    return OutputPublicSample.from_kgrel(sample).to_dict()


@router.post("/samples")
def create_sample(
    create_sample: Annotated[InputPublicSample, Body()],
    sample_service: SampleServiceDep,
    user: CurrentUserDep,
):
    _validate_create_or_update(create_sample)

    expected_public_id = make_sample_id(
        create_sample.mineral_site_id, create_sample.sample_id  # type: ignore
    )
    if sample_service.get_sample_db_id(expected_public_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The sample already exists.",
        )

    new_sample = create_sample.to_kgrel()
    try:
        created = sample_service.create(new_sample, user.get_uri())
    except ArgumentError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        )
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Mineral site {create_sample.mineral_site_id} does not exist.",
        )
    return OutputPublicSample.from_kgrel(created).to_dict()


@router.put("/samples/{sample_id}")
def update_sample(
    sample_id: InternalID,
    update_sample: InputPublicSample,
    sample_service: SampleServiceDep,
    user: CurrentUserDep,
    snapshot_id: Annotated[Optional[int], Query()] = None,
):
    _validate_create_or_update(update_sample)

    expected_public_id = make_sample_id(
        update_sample.mineral_site_id, update_sample.sample_id  # type: ignore
    )
    if sample_id != expected_public_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The sample_id in the request body does not match the sample_id in the URL.",
        )

    sample_db_id = sample_service.get_sample_db_id(sample_id)
    if sample_db_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The sample doesn't exist",
        )

    upd_sample = update_sample.to_kgrel()
    upd_sample.public_id = sample_id
    upd_sample.id = sample_db_id

    try:
        updated = sample_service.update(upd_sample, user.get_uri(), snapshot_id)
    except ExpiredSnapshotIdError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except SampleNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    return OutputPublicSample.from_kgrel(updated).to_dict()


@router.patch("/samples/{sample_id}")
def patch_sample(
    sample_id: InternalID,
    patch: Annotated[dict, Body()],
    sample_service: SampleServiceDep,
    user: CurrentUserDep,
    snapshot_id: Annotated[Optional[int], Query()] = None,
):
    """Apply a sparse, keyed upsert to an existing sample -- only send the fields
    that actually changed. Top-level Sample fields are matched by name; nested
    `analyses`/`elements` are matched to existing records by `analysis_id`/
    `label` (not array index), so only include the analysis/element being
    touched plus its own field(s). An `analysis_id`/`label` that doesn't match
    an existing record creates a new one instead of erroring -- the sample
    itself must already exist (this route addresses one by `sample_id` in the
    URL; see POST /papers/publish for creating whole new samples in a batch).
    See ta2-table-understanding issue #18 for the full contract.
    """
    _validate_patch_units(patch)

    try:
        updated = sample_service.patch(sample_id, patch, user.get_uri(), snapshot_id)
    except SampleNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ExpiredSnapshotIdError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except ArgumentError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        )
    return OutputPublicSample.from_kgrel(updated).to_dict()


@router.post("/papers/publish")
def publish_paper(
    payload: Annotated[dict, Body()],
    sample_service: SampleServiceDep,
    user: CurrentUserDep,
):
    """Batch upsert for one paper's worth of sample/analysis/element edits and
    creates in a single call -- the endpoint GeoChem HMI's Export/Publish action
    calls. `:MineralResourcePaper` is a real GeoChem ontology class (see
    GeoChem ontology), which is why this route is namespaced under /papers/
    even though MinMod itself persists no Paper row -- the paper is this
    request's addressing envelope, not a stored record. Body shape:
    `{"paper_id": ..., "deposits": [{"mineral_site_id": ..., "samples": [...]}]}`,
    sparse -- only touched deposits/samples/analyses/elements need to appear. An
    unmatched `sample_id`/`analysis_id`/element `symbol` creates a new record; an
    unresolvable `mineral_site_id` does not (that's a new MineralSite, out of
    scope here -- publish the deposit first). Each sample applies as its own
    transaction, so one bad sample doesn't block the rest of the batch. See
    ta2-table-understanding issue #18 for the full contract.
    """
    try:
        return sample_service.publish(payload, user.get_uri())
    except ArgumentError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


def _validate_patch_units(patch: dict):
    """Mutates `patch` in place (see SampleService._resolve_element_units --
    bare unit labels/URIs, issue #18 §5) then validates the resulting wrapped
    shape's normalized_uri against known units. Delegates to SampleService's
    own versions of both steps rather than keeping a second, easily-drifting
    copy of this logic here."""
    try:
        SampleService._resolve_element_units(patch)
        SampleService._validate_units(patch)
    except ArgumentError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        )


@router.post("/samples/validate")
def validate_sample(sample: Annotated[dict, Body()]):
    """Validate the sample data structure and properties submitted in the request body."""
    try:
        _validate_sample([sample], EntityService.get_instance())
    except ValueError as e:
        cause_str = f". Caused by: {str(e.__cause__)}" if e.__cause__ else ""
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e) + cause_str,
        )
    return {"message": "Validation successful"}


def _validate_create_or_update(sample: InputPublicSample):
    if not sample.sample_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="sample_id is required.",
        )
