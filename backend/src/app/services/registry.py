"""Model and workflow registry.

Capability checks are **data-driven** (AGENTS.md): a model's requirements and
supported capabilities live in this registry and are matched against GPU
capability records produced by the registry/probe path. There is no
``if "1080" in device_name`` anywhere in the codebase.

A registry entry is a *declaration*, not a hard-coded model list: deployments
load their catalogue from the database and merge it with these built-ins. The
built-ins exist so the dev/CI stack works end to end without downloading a
single weight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from worker.gpu.base import Capability


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """A model catalogue entry."""

    key: str
    name: str
    family: str
    job_type: str = "IMAGE"
    required_capabilities: tuple[str, ...] = ()
    min_vram_mb: int = 0
    default_params: dict[str, Any] = field(default_factory=dict)
    est_seconds: float = 30.0
    precision: str = "fp16"
    source: str = "builtin"
    notes: str = ""

    def supports(self, capabilities: dict[str, Any]) -> bool:
        return all(bool(capabilities.get(name, False)) for name in self.required_capabilities)


@dataclass(frozen=True, slots=True)
class WorkflowSpec:
    """A workflow graph version."""

    key: str
    name: str
    version: str
    graph: dict[str, Any] = field(default_factory=dict)
    model_keys: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    min_vram_mb: int = 0
    source: str = "builtin"

    def node_count(self) -> int:
        nodes = self.graph.get("nodes")
        return len(nodes) if isinstance(nodes, list) else 0


#: Built-in catalogue. Deliberately small and honest: these describe *shapes*
#: of workload, and the mock engine satisfies them without any weights.
BUILTIN_MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        key="builtin.mock.text2image",
        name="Mock Text-to-Image (no weights)",
        family="mock",
        job_type="IMAGE",
        required_capabilities=(Capability.TEXT_TO_IMAGE.value,),
        min_vram_mb=0,
        default_params={"steps": 4, "width": 512, "height": 512, "cfg_scale": 7.0},
        est_seconds=2.0,
        source="builtin",
        notes="Executes on MockGpuBackend so CI needs no model download.",
    ),
    ModelSpec(
        key="builtin.mock.image2image",
        name="Mock Image-to-Image (no weights)",
        family="mock",
        job_type="IMAGE",
        required_capabilities=(Capability.IMAGE_TO_IMAGE.value,),
        min_vram_mb=0,
        default_params={"steps": 4, "strength": 0.6},
        est_seconds=2.0,
        source="builtin",
    ),
    ModelSpec(
        key="builtin.mock.video",
        name="Mock Text-to-Video (no weights)",
        family="mock",
        job_type="VIDEO",
        required_capabilities=(),
        min_vram_mb=0,
        default_params={"frames": 8, "fps": 8},
        est_seconds=4.0,
        source="builtin",
    ),
    ModelSpec(
        key="builtin.mock.audio",
        name="Mock Text-to-Audio (no weights)",
        family="mock",
        job_type="AUDIO",
        required_capabilities=(),
        min_vram_mb=0,
        default_params={"duration_seconds": 2},
        est_seconds=2.0,
        source="builtin",
    ),
)

BUILTIN_WORKFLOWS: tuple[WorkflowSpec, ...] = (
    WorkflowSpec(
        key="builtin.mock.text2image",
        name="Mock text-to-image",
        version="1.0.0",
        graph={
            "nodes": [
                {"id": 1, "type": "CheckpointLoader", "model": "builtin.mock.text2image"},
                {"id": 2, "type": "CLIPTextEncode", "text": "{{prompt}}"},
                {"id": 3, "type": "EmptyLatentImage", "width": 512, "height": 512},
                {"id": 4, "type": "KSampler", "steps": 4},
                {"id": 5, "type": "VAEDecode"},
                {"id": 6, "type": "SaveImage", "role": "output"},
            ],
            "links": [
                [1, 2],
                [1, 3],
                [2, 4],
                [3, 4],
                [4, 5],
                [5, 6],
            ],
        },
        model_keys=("builtin.mock.text2image",),
        required_capabilities=(Capability.TEXT_TO_IMAGE.value,),
        min_vram_mb=0,
    ),
    WorkflowSpec(
        key="builtin.mock.image2image",
        name="Mock image-to-image",
        version="1.0.0",
        graph={
            "nodes": [
                {"id": 1, "type": "CheckpointLoader", "model": "builtin.mock.image2image"},
                {"id": 2, "type": "LoadImage", "key": "{{input_image}}"},
                {"id": 3, "type": "VAEEncode"},
                {"id": 4, "type": "KSampler", "steps": 4},
                {"id": 5, "type": "VAEDecode"},
                {"id": 6, "type": "SaveImage", "role": "output"},
            ],
            "links": [[1, 2], [2, 3], [1, 3], [3, 4], [4, 5], [5, 6]],
        },
        model_keys=("builtin.mock.image2image",),
        required_capabilities=(Capability.IMAGE_TO_IMAGE.value,),
    ),
    WorkflowSpec(
        key="builtin.mock.video",
        name="Mock text-to-video",
        version="1.0.0",
        graph={
            "nodes": [
                {"id": 1, "type": "CheckpointLoader", "model": "builtin.mock.video"},
                {"id": 2, "type": "CLIPTextEncode", "text": "{{prompt}}"},
                {"id": 3, "type": "EmptyLatentVideo", "frames": 8, "fps": 8},
                {"id": 4, "type": "Sampler", "steps": 4},
                {"id": 5, "type": "VAEDecode"},
                {"id": 6, "type": "SaveVideo", "role": "output"},
            ],
            "links": [[1, 2], [1, 3], [2, 4], [3, 4], [4, 5], [5, 6]],
        },
        model_keys=("builtin.mock.video",),
        required_capabilities=(),
        min_vram_mb=0,
    ),
    WorkflowSpec(
        key="builtin.mock.audio",
        name="Mock text-to-audio",
        version="1.0.0",
        graph={
            "nodes": [
                {"id": 1, "type": "CheckpointLoader", "model": "builtin.mock.audio"},
                {"id": 2, "type": "TextEncode", "text": "{{prompt}}"},
                {"id": 3, "type": "Synthesise", "duration_seconds": 2},
                {"id": 4, "type": "SaveAudio", "role": "output"},
            ],
            "links": [[1, 2], [2, 3], [3, 4]],
        },
        model_keys=("builtin.mock.audio",),
        required_capabilities=(),
        min_vram_mb=0,
    ),
)


class ModelRegistry:
    """In-memory catalogue lookup, mergeable with database-backed entries."""

    def __init__(
        self,
        models: tuple[ModelSpec, ...] | None = None,
        workflows: tuple[WorkflowSpec, ...] | None = None,
    ) -> None:
        self._models: dict[str, ModelSpec] = {m.key: m for m in (models or BUILTIN_MODELS)}
        self._workflows: dict[str, WorkflowSpec] = {
            w.key: w for w in (workflows or BUILTIN_WORKFLOWS)
        }

    def register_model(self, spec: ModelSpec) -> None:
        self._models[spec.key] = spec

    def register_workflow(self, spec: WorkflowSpec) -> None:
        self._workflows[spec.key] = spec

    def get_model(self, key: str) -> ModelSpec | None:
        return self._models.get(key)

    def get_workflow(self, key: str) -> WorkflowSpec | None:
        return self._workflows.get(key)

    def list_models(self) -> list[ModelSpec]:
        return sorted(self._models.values(), key=lambda spec: spec.key)

    def list_workflows(self) -> list[WorkflowSpec]:
        return sorted(self._workflows.values(), key=lambda spec: spec.key)

    def select_workflow_for(
        self, job_type: str, *, model_key: str | None = None
    ) -> WorkflowSpec | None:
        """Default workflow for a job type — a data lookup, not a conditional tree.

        Resolution order: a workflow that declares the chosen model, then any
        workflow that declares *some* model of this job type, then a workflow
        with no model binding. Never falls back to a workflow of a *different*
        job type, because its capability requirements would be wrong (a video job
        must not be forced to satisfy ``text_to_image``).
        """
        model = self.get_model(model_key) if model_key else None
        if model is None:
            model = self.default_model_for(job_type)
        candidates = [
            wf
            for wf in self._workflows.values()
            if job_type.upper() in self.job_types_for_workflow(wf)
        ]
        if not candidates:
            return None
        if model is not None:
            for workflow in candidates:
                if model.key in workflow.model_keys:
                    return workflow
        for workflow in candidates:
            if not workflow.model_keys:
                return workflow
        return candidates[0]

    def job_types_for_workflow(self, workflow: WorkflowSpec) -> frozenset[str]:
        """Job types a workflow can serve, derived from the models it binds.

        Derived rather than declared so a workflow can never claim a job type its
        models do not support; a workflow with no bound model serves any type.
        """
        bound = [self.get_model(key) for key in workflow.model_keys]
        job_types = {model.job_type.upper() for model in bound if model is not None}
        if not job_types and not workflow.model_keys:
            return frozenset(
                model.job_type.upper() for model in self._models.values()
            ) | {""}
        return frozenset(job_types)

    def default_model_for(self, job_type: str) -> ModelSpec | None:
        """First declared model for a job type.

        Declaration order is significant: the catalogue lists the primary
        workload of a type first (text-to-image before image-to-image), so the
        default is the sensible one and is overridable per request.
        """
        for model in self._models.values():
            if model.job_type == job_type.upper():
                return model
        return None

    def requirements_for(
        self, *, model_key: str | None, workflow_key: str | None
    ) -> dict[str, Any]:
        """VRAM + capability requirements for a (model, workflow) pair.

        This is what the Scheduler uses to place a job; the result is *data*, so
        an operator can add a model without touching scheduling code.
        """
        model = self.get_model(model_key) if model_key else None
        workflow = self.get_workflow(workflow_key) if workflow_key else None
        capabilities = set()
        if model:
            capabilities.update(model.required_capabilities)
        if workflow:
            capabilities.update(workflow.required_capabilities)
        vram = max(model.min_vram_mb if model else 0, workflow.min_vram_mb if workflow else 0)
        return {
            "required_vram_mb": vram,
            "required_capabilities": sorted(capabilities),
            "model": model.key if model else None,
            "workflow": workflow.key if workflow else None,
        }


_registry = ModelRegistry()


def get_registry() -> ModelRegistry:
    return _registry


__all__ = [
    "BUILTIN_MODELS",
    "BUILTIN_WORKFLOWS",
    "ModelRegistry",
    "ModelSpec",
    "WorkflowSpec",
    "get_registry",
]
