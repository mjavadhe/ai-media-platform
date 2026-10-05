"""Model/workflow catalogue endpoints (``/api/v1/models``).

Read-only: the catalogue is *data*. Adding a model or workflow is a registry
change (declaration or config file), never a code change in the scheduler.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import DbSession, RegistryDep, require_permissions
from app.api.v1.schemas import (
    ModelListOut,
    ModelSpecOut,
    WorkflowListOut,
    WorkflowSpecOut,
)
from app.domain.enums import PermissionCode

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=ModelListOut)
async def list_models(
    session: DbSession,
    registry: RegistryDep,
    principal: require_permissions(PermissionCode.MODEL_READ.value),
    job_type: str | None = None,
) -> ModelListOut:
    models = registry.list_models()
    if job_type:
        wanted = job_type.upper()
        models = [model for model in models if model.job_type == wanted]
    return ModelListOut(
        items=[
            ModelSpecOut(
                key=model.key,
                name=model.name,
                family=model.family,
                job_type=model.job_type,
                required_capabilities=list(model.required_capabilities),
                min_vram_mb=model.min_vram_mb,
                est_seconds=model.est_seconds,
                precision=model.precision,
                source=model.source,
                notes=model.notes,
            )
            for model in models
        ]
    )


@router.get("/workflows", response_model=WorkflowListOut)
async def list_workflows(
    session: DbSession,
    registry: RegistryDep,
    principal: require_permissions(PermissionCode.MODEL_READ.value),
) -> WorkflowListOut:
    return WorkflowListOut(
        items=[
            WorkflowSpecOut(
                key=workflow.key,
                name=workflow.name,
                version=workflow.version,
                model_keys=list(workflow.model_keys),
                required_capabilities=list(workflow.required_capabilities),
                min_vram_mb=workflow.min_vram_mb,
                source=workflow.source,
            )
            for workflow in registry.list_workflows()
        ]
    )


@router.get("/requirements")
async def requirements(
    session: DbSession,
    registry: RegistryDep,
    principal: require_permissions(PermissionCode.MODEL_READ.value),
    job_type: str,
    model_key: str | None = None,
    workflow_key: str | None = None,
) -> dict:
    """What the Scheduler will require for this (job type, model, workflow).

    Exposed so the UI can explain *why* a job is waiting instead of leaving the
    user guessing.
    """
    workflow = (
        registry.get_workflow(workflow_key)
        if workflow_key
        else registry.select_workflow_for(job_type, model_key=model_key)
    )
    model = registry.get_model(model_key) if model_key else registry.default_model_for(job_type)
    if model_key and model is None:
        raise HTTPException(status_code=404, detail=f"unknown model {model_key!r}")
    if workflow_key and workflow is None:
        raise HTTPException(status_code=404, detail=f"unknown workflow {workflow_key!r}")
    result = registry.requirements_for(
        model_key=model.key if model else None, workflow_key=workflow.key if workflow else None
    )
    return {
        "job_type": job_type.upper(),
        "model": result["model"],
        "workflow": result["workflow"],
        "required_vram_mb": result["required_vram_mb"],
        "required_capabilities": result["required_capabilities"],
        "node_count": workflow.node_count() if workflow else 1,
    }


__all__ = ["router"]
