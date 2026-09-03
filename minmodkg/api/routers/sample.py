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
