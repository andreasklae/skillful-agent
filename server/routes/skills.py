"""Skill endpoints: upload archives and list registered skills."""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from server.dependencies import get_agent
from server.models import SkillSummaryResponse, SkillUploadResponse
from server.services.archive import extract_skill_archive, find_uploaded_skill_dir

logger = logging.getLogger(__name__)

router = APIRouter(tags=["skills"])


@router.post("/skills/upload", response_model=SkillUploadResponse)
async def upload_skill_archive(
    file: UploadFile = File(...), agent=Depends(get_agent)
) -> SkillUploadResponse:
    """Upload a single skill archive and register it in the live agent."""
    filename = file.filename or "skill-upload"
    logger.info("HTTP /skills/upload received filename=%s", filename)
    archive_bytes = await file.read()
    if not archive_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    with tempfile.TemporaryDirectory(prefix="skill-upload-") as temp_dir:
        extract_root = Path(temp_dir) / "extract"
        extract_root.mkdir(parents=True, exist_ok=True)

        try:
            extract_skill_archive(filename, archive_bytes, extract_root)
            extracted_skill_dir = find_uploaded_skill_dir(extract_root)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        relative_dir = extracted_skill_dir.relative_to(extract_root)
        destination = agent.skills_dir / relative_dir
        logger.info(
            "HTTP /skills/upload extracted skill_dir=%s destination=%s",
            extracted_skill_dir,
            destination,
        )
        if destination.exists():
            raise HTTPException(
                status_code=409,
                detail=f"Skill destination already exists: {destination}",
            )

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(extracted_skill_dir, destination)

    try:
        skill_name = agent.add_skill_dir(destination)
        registered_skills = sorted(agent._skills)
        logger.info(
            "HTTP /skills/upload registered skill_name=%s destination=%s total_skills=%s",
            skill_name,
            destination,
            len(registered_skills),
        )
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise

    return SkillUploadResponse(
        skill_name=skill_name,
        skill_dir=str(destination),
        registered_skills=registered_skills,
    )


@router.get("/skills", response_model=list[SkillSummaryResponse])
async def list_skills(agent=Depends(get_agent)) -> list[SkillSummaryResponse]:
    logger.info("HTTP /skills returning count=%s", len(agent._skills))
    return [
        SkillSummaryResponse(
            name=skill.name,
            description=skill.description,
            path=str(skill.path) if skill.path is not None else None,
            scripts=skill.scripts,
            references=skill.references,
            assets=skill.assets,
        )
        for skill in sorted(agent._skills.values(), key=lambda s: s.name)
    ]
