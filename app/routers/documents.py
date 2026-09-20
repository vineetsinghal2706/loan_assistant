"""Applicant document upload and field extraction."""

from __future__ import annotations

import json
from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.config import get_settings
from app.extraction.applicant_extractor import ApplicantExtractor
from app.extraction.parsers import UnsupportedDocument, parse_bytes, parse_path
from app.observability import metrics
from app.schemas import ApplicantProfile, ExtractionResult

router = APIRouter(prefix="/documents", tags=["documents"])

REQUIRED_FIELD_HINT = (
    "Fields still missing are listed in 'missing_fields'. A hard criterion that "
    "cannot be evaluated produces INSUFFICIENT_INFORMATION, never a decline."
)


def _record_metrics(result: ExtractionResult) -> None:
    metrics.extraction_documents_total.labels(parser=result.parser, result="ok").inc()
    for field in result.fields:
        metrics.extraction_fields_total.labels(field=field.field_name, result="found").inc()
    for name in result.missing_fields:
        metrics.extraction_fields_total.labels(field=name, result="missing").inc()


@router.post(
    "/extract",
    response_model=ExtractionResult,
    summary="Extract applicant information from an uploaded document",
)
async def extract(
    file: UploadFile = File(..., description="PDF, TXT, MD, CSV or JSON applicant pack"),
    base_applicant: Optional[str] = Form(
        default=None,
        description="Optional JSON ApplicantProfile; extracted values overlay it.",
    ),
) -> ExtractionResult:
    data = await file.read()
    base: Optional[ApplicantProfile] = None
    if base_applicant:
        try:
            base = ApplicantProfile(**json.loads(base_applicant))
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail=f"base_applicant is not a valid ApplicantProfile: {exc}"
            ) from exc

    try:
        document = parse_bytes(file.filename or "upload", data)
    except UnsupportedDocument as exc:
        metrics.extraction_documents_total.labels(parser="unknown", result="rejected").inc()
        raise HTTPException(status_code=415, detail=str(exc)) from exc

    result = ApplicantExtractor().extract(document, base=base)
    result.warnings.append(REQUIRED_FIELD_HINT)
    _record_metrics(result)
    return result


@router.get("/samples", summary="List the bundled synthetic applicant documents")
def samples() -> dict:
    directory = get_settings().applicants_dir
    files: List[str] = []
    if directory.exists():
        files = sorted(path.name for path in directory.iterdir() if path.is_file())
    return {
        "notice": "DEMO BANK - SYNTHETIC DOCUMENT - FOR EDUCATIONAL USE ONLY",
        "documents": files,
    }


@router.post(
    "/samples/{name}/extract",
    response_model=ExtractionResult,
    summary="Extract from a bundled synthetic applicant document",
)
def extract_sample(name: str) -> ExtractionResult:
    directory = get_settings().applicants_dir
    path = (directory / name).resolve()
    if directory.resolve() not in path.parents or not path.exists():
        raise HTTPException(status_code=404, detail=f"Unknown sample document '{name}'")
    result = ApplicantExtractor().extract(parse_path(path))
    _record_metrics(result)
    return result
