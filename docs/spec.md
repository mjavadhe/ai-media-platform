# AI MEDIA GENERATION & GPU ORCHESTRATION PLATFORM

## Master Project Specification

### Version 1.0

---

# 1. PROJECT MISSION

Build a production-grade, extensible, multi-project AI media generation and GPU orchestration platform.

The platform must provide a unified web-based control plane for managing:

* GPU infrastructure
* GPU nodes
* AI workers
* AI models
* model versions
* workflows
* image generation
* video generation
* projects
* sequences
* shots
* characters
* locations
* assets
* reference media
* generated media
* jobs
* queues
* scheduling
* logs
* monitoring
* storage
* users
* permissions
* system configuration

The platform must be designed as a **general-purpose AI media production system**.

It must NOT be tied to any specific film, story, genre, character, GPU model, AI model, or vendor.

---

# 2. IMPORTANT PROJECT PRINCIPLE

A project such as:

> "Iran vs Rome"

may be used as an example project during development and testing.

However, the platform itself must have absolutely no hard-coded dependency on:

* Iran
* Rome
* historical warfare
* specific characters
* specific locations
* historical content
* a specific visual style
* a specific GPU
* a specific AI model
* a specific workflow
* a specific inference engine

The platform must be capable of managing completely unrelated projects.

Examples:

* Historical films
* Sci-fi films
* Fantasy films
* Modern films
* Advertisements
* Product videos
* Music videos
* Short films
* Documentaries
* YouTube content
* Social media content
* Game cinematics
* Architecture visualization
* Concept art
* Character design
* Animation
* Commercial media production

---

# 3. CORE DESIGN PHILOSOPHY

The platform must be:

* Multi-project
* Multi-user
* Multi-node
* Multi-GPU
* GPU-agnostic
* Model-agnostic
* Workflow-agnostic
* Engine-agnostic
* API-first
* Extensible
* Fault-tolerant
* Observable
* Reproducible
* Versioned
* Secure
* Production-ready

The architecture must support growth without requiring a rewrite of the core platform.

---

# 4. HARDWARE INDEPENDENCE

The initial development environment may contain:

```text
5 × NVIDIA GTX 1080 Ti
11 GB VRAM each
```

However, this is ONLY initial hardware.

The platform must also support future environments such as:

```text
1 × RTX 3090
2 × RTX 3090
1 × RTX 4090
2 × RTX 4090
10 × RTX 4090
A100
A100 40GB
A100 80GB
A40
L40
L40S
H100
Future NVIDIA GPUs
```

Mixed GPU environments must be supported.

Example:

```text
Node-01
    GPU-01 GTX 1080 Ti 11GB
    GPU-02 GTX 1080 Ti 11GB
    GPU-03 GTX 1080 Ti 11GB
    GPU-04 GTX 1080 Ti 11GB
    GPU-05 GTX 1080 Ti 11GB

Node-02
    GPU-01 RTX 4090 24GB

Node-03
    GPU-01 RTX 3090 24GB
    GPU-02 RTX 3090 24GB
```

The platform must automatically discover and manage this environment.

---

# 5. GPU DISCOVERY

Every worker node must automatically discover its hardware.

The system must collect at minimum:

```text
Node ID
GPU ID
GPU UUID
GPU Name
GPU Vendor
VRAM Total
VRAM Available
Driver Version
CUDA Version
Compute Capability
PCI Bus ID
Temperature
Power Usage
Power Limit
GPU Utilization
Memory Utilization
Running Processes
Health Status
Current Job
Capabilities
```

The user must NOT manually register individual GPUs.

---

# 6. GPU CAPABILITY SYSTEM

GPU discovery alone is not sufficient.

The platform must maintain a dynamic capability system.

Example:

```text
GPU:
    RTX 4090

Capabilities:
    Image Generation
    Image-to-Image
    Image-to-Video
    Video Generation
    Upscaling
    Model X
    Model Y
```

Another GPU may support only a subset.

Capabilities must be determined through:

* hardware information
* VRAM requirements
* CUDA compatibility
* driver compatibility
* model requirements
* runtime compatibility
* optional benchmark/probe jobs

Do not hard-code GPU capability decisions in application business logic.

---

# 7. GPU RESOURCE MANAGEMENT

Each GPU must have a lifecycle state.

Possible states:

```text
AVAILABLE
BUSY
RESERVED
DRAINING
UNHEALTHY
OFFLINE
MAINTENANCE
```

The scheduler must prevent conflicting jobs from using the same GPU.

GPU allocation must support:

* automatic allocation
* manual allocation
* GPU pinning
* node pinning
* capability-based allocation
* priority-based allocation
* resource reservation

---

# 8. MULTI-GPU DISTINCTION

The system MUST distinguish between:

## Parallel Jobs

Example:

```text
GPU-01 → Shot 001
GPU-02 → Shot 002
GPU-03 → Shot 003
GPU-04 → Shot 004
```

and:

## Multi-GPU Inference

Example:

```text
Job 001
    ↓
GPU-01 + GPU-02 + GPU-03
```

The platform must NEVER assume that a model can use multiple GPUs.

Multi-GPU execution must depend on the model and inference engine capabilities.

---

# 9. NODE ARCHITECTURE

The platform must use a distributed architecture.

Each node should run an AI Worker Agent.

Conceptually:

```text
AI Media Control Plane
        |
        v
   Orchestrator
        |
        v
     Scheduler
        |
        v
    Job Queue
        |
        +------------------+
        |                  |
        v                  v
    Worker Node 01     Worker Node 02
        |                  |
        v                  v
      GPUs               GPUs
```

---

# 10. WORKER AGENT

Each Worker Agent must provide:

* node registration
* GPU discovery
* capability reporting
* heartbeat
* health monitoring
* job execution
* progress reporting
* model management
* workflow execution
* result upload
* log forwarding
* metrics reporting
* failure reporting

Worker lifecycle:

```text
Install
    ↓
Connect to Controller
    ↓
Authenticate
    ↓
Discover Hardware
    ↓
Discover GPUs
    ↓
Determine Capabilities
    ↓
Register Node
    ↓
Heartbeat
    ↓
READY
```

---

# 11. ORCHESTRATOR

The Orchestrator is the central control component.

Responsibilities:

* job management
* queue management
* scheduling
* GPU allocation
* node selection
* model selection
* workflow selection
* resource management
* retries
* failure recovery
* worker management
* execution tracking
* project coordination

---

# 12. SCHEDULER

The scheduler must select the best available resource based on:

* required VRAM
* model requirements
* workflow requirements
* GPU capability
* node availability
* current GPU load
* model cache availability
* user-selected GPU
* node selection
* job priority
* scheduling policy

Supported policies should include:

```text
AUTO
FASTEST
LOWEST_LOAD
LOWEST_RESOURCE_COST
CAPABILITY_MATCH
MODEL_CACHE_AWARE
ROUND_ROBIN
MANUAL
```

The scheduler must be extensible so additional policies can be added later.

---

# 13. JOB SYSTEM

Every generation must become a Job.

Job states:

```text
CREATED
QUEUED
RESERVED
STARTING
RUNNING
PAUSED
RETRYING
COMPLETED
FAILED
CANCELLED
```

Every job must store:

```text
Job ID
Project ID
Sequence ID
Shot ID
Job Type
Priority
User
Model
Model Version
Workflow
Workflow Version
Input Assets
GPU
Node
Parameters
Prompt
Status
Progress
Start Time
End Time
Duration
Error
Output Assets
```

---

# 14. JOB RETRY

If a job fails:

```text
Job Failed
    ↓
Analyze Failure
    ↓
Retry Policy
    ↓
Select Compatible Resource
    ↓
Retry
```

Example:

```text
GPU-03
CUDA Out Of Memory
        ↓
Retry
        ↓
RTX 4090
        ↓
Success
```

The retry system must prevent infinite retry loops.

---

# 15. WEB CONTROL PANEL

Build a modern production-quality web application.

Do NOT rely on:

* default Django admin
* basic HTML forms
* raw framework-generated interfaces
* primitive dashboards

The UI must be:

* modern
* responsive
* component-based
* fast
* usable
* professional
* scalable
* RTL-ready
* localization-ready

---

# 16. MAIN NAVIGATION

At minimum:

```text
Dashboard

Projects
    Projects
    Sequences
    Shots

Assets
    Characters
    Locations
    Props
    Weapons
    Vehicles
    Buildings
    Creatures
    Reference Images
    Reference Videos
    Other Assets

Image Generator

Video Generator

Jobs
    Queue
    Running
    Completed
    Failed

GPU Cluster
    Nodes
    GPUs
    Health

Models
    Installed
    Available
    Versions
    Registry

Workflows

Logs

Storage

Users

Settings
```

---

# 17. DASHBOARD

The dashboard must provide a real-time overview.

Show:

```text
Total Nodes
Online Nodes
Offline Nodes
Total GPUs
Available GPUs
Busy GPUs
GPU Utilization
VRAM Utilization
Running Jobs
Queued Jobs
Failed Jobs
Completed Jobs
Active Models
Storage Usage
System Health
```

Include useful charts and tables.

---

# 18. REAL-TIME MONITORING

The UI must not require manual page refreshes for dynamic information.

Use an appropriate real-time mechanism such as:

* WebSockets
* Server-Sent Events
* equivalent real-time architecture

Real-time information should include:

```text
GPU status
GPU utilization
VRAM usage
temperature
power
jobs
queue
worker health
logs
progress
```

---

# 19. GPU DASHBOARD

For each GPU display:

```text
GPU Name
GPU UUID
Node
VRAM Total
VRAM Used
VRAM Free
GPU Utilization
Memory Utilization
Temperature
Power
Current Job
Current Model
Health
Status
```

Allow historical metrics where appropriate.

---

# 20. PROJECT MANAGEMENT

A Project is the highest-level media production entity.

Each Project can contain:

```text
Project
    ├── Metadata
    ├── Characters
    ├── Locations
    ├── Assets
    ├── Sequences
    ├── Shots
    ├── Images
    ├── Videos
    ├── Workflows
    ├── Generation History
    └── Exports
```

Projects must be independent.

---

# 21. CHARACTER SYSTEM

Characters must be first-class entities.

Character data may include:

```text
Character ID
Name
Description
Age
Gender
Appearance
Face
Hair
Body
Clothing
Armor
Accessories
Personality
Voice
Reference Images
Reference Videos
Versions
Tags
Metadata
```

Characters must be reusable across projects.

---

# 22. LOCATION SYSTEM

Locations must be first-class entities.

Example metadata:

```text
Location ID
Name
Description
Architecture
Environment
Lighting
Weather
Time of Day
Season
Reference Images
Reference Videos
Versions
Tags
Metadata
```

Locations must be reusable.

---

# 23. GENERAL ASSET SYSTEM

The platform must support arbitrary assets.

Examples:

```text
Character
Location
Object
Prop
Weapon
Vehicle
Building
Creature
Animal
Costume
Reference Image
Reference Video
Audio
Other
```

Assets must support:

* versioning
* tagging
* metadata
* references
* reuse
* project association

---

# 24. SHARED VS PROJECT ASSETS

Assets may be:

```text
GLOBAL
PROJECT_SPECIFIC
```

Global assets can be reused across multiple projects.

Project-specific assets belong only to one project unless explicitly promoted/shared.

---

# 25. IMAGE GENERATOR

The platform must support image generation through configurable workflows.

Possible modes:

```text
Text-to-Image
Image-to-Image
Reference-to-Image
Inpainting
Outpainting
Upscaling
```

Actual supported modes depend on the installed model and workflow.

---

# 26. IMAGE GENERATION INPUTS

Allow:

```text
Project
Character
Location
Assets
Prompt
Negative Prompt
Model
Model Version
Workflow
Workflow Version
Seed
Resolution
Steps
CFG
Batch Size
Reference Images
```

The UI should expose only parameters supported by the selected workflow.

---

# 27. PROMPT SYSTEM

Prompts must not be treated as simple strings only.

Build a composable prompt system.

Conceptually:

```text
Base Style
+
Character
+
Location
+
Action
+
Camera
+
Lighting
+
Mood
+
Environment
+
Visual Style
+
Technical Parameters
```

Generate a final prompt from these components.

---

# 28. PROMPT TEMPLATES

Users should be able to save:

```text
Prompt Templates
Negative Prompt Templates
Style Templates
Camera Templates
Lighting Templates
```

Templates must be reusable and versioned.

---

# 29. AI PROMPT ASSISTANT

The architecture should support an optional LLM service for:

* prompt expansion
* prompt cleanup
* shot descriptions
* character descriptions
* location descriptions
* camera suggestions
* negative prompt generation
* consistency checks
* shot planning

The LLM provider must be abstracted.

Do not hard-code the platform to a single AI provider.

---

# 30. SHOT SYSTEM

A Shot is a core production entity.

Each Shot should contain:

```text
Shot ID
Project
Sequence
Title
Description
Duration
Characters
Locations
Assets
Reference Images
Reference Videos
Prompt
Negative Prompt
Camera
Lens
Movement
Lighting
Weather
Time of Day
Style
Model
Workflow
Seed
Status
Generation History
Outputs
```

---

# 31. SHOT GENERATOR

Users must be able to create shots individually or in batches.

Example:

```text
Project:
Example Project

Sequence:
Opening Scene

Number of Shots:
20

Duration:
5 seconds

Style:
Cinematic

Character:
Character A

Location:
Location A
```

The system should create the required jobs automatically.

---

# 32. BATCH GENERATION

Users must be able to generate:

```text
1 Shot
5 Shots
20 Shots
100 Shots
```

or any configurable amount.

The system must automatically create and queue jobs.

---

# 33. VIDEO GENERATOR

The Video Generator must use Shots and Assets as inputs.

Possible modes:

```text
Image-to-Video
Text-to-Video
Video-to-Video
Reference-to-Video
Frame Interpolation
Video Upscaling
```

Support depends on installed models.

---

# 34. VIDEO GENERATION INPUTS

Allow:

```text
Shot
Reference Image
Reference Video
Prompt
Negative Prompt
Character
Location
Model
Model Version
Workflow
Workflow Version
Duration
FPS
Resolution
Seed
```

---

# 35. GPU SELECTION FOR VIDEO

Allow users to select:

```text
AUTO
Specific GPU
Specific Node
GPU Set
```

Example:

```text
GPU-01
```

or:

```text
GPU-01 + GPU-03 + GPU-05
```

But validate the selection against the model and workflow capabilities.

---

# 36. MODEL REGISTRY

Every model must have structured metadata.

At minimum:

```text
Model ID
Name
Version
Type
Provider
Repository
License
File Size
VRAM Requirement
Recommended VRAM
Precision
Quantization
Supported Tasks
Supported Engines
Supported GPUs
Multi-GPU Support
Dependencies
Workflow
Checksum
Installation Method
```

---

# 37. MODEL TYPES

The registry should support categories such as:

```text
Image Generation
Video Generation
Image Upscaling
Video Upscaling
Image Editing
Segmentation
Control Models
Embedding Models
LLM
Vision Models
Audio
Other
```

---

# 38. MODEL MANAGER

Users must be able to:

```text
Install
Download
Update
Remove
Enable
Disable
Validate
Test
Set Default
Version
Rollback
```

Model installation must validate:

* disk space
* VRAM requirements
* runtime dependencies
* CUDA compatibility
* engine compatibility
* checksum where available

---

# 39. MODEL CACHE

Workers should maintain a local model cache.

The scheduler should be model-cache-aware.

If two compatible GPUs are available, prefer the node that already contains the required model when doing so does not violate higher-priority scheduling rules.

---

# 40. WORKFLOW REGISTRY

Workflows must be separate entities.

Example:

```text
Workflow:
cinematic-image-v1

Engine:
ComfyUI

Model:
Model-X

Inputs:
Prompt
Negative Prompt
Seed
Reference
Resolution
```

Workflows must be versioned.

---

# 41. COMFYUI INTEGRATION

ComfyUI should be treated as an inference/workflow engine.

Architecture:

```text
Web UI
    ↓
API
    ↓
Orchestrator
    ↓
Workflow Registry
    ↓
ComfyUI Adapter
    ↓
ComfyUI
    ↓
GPU
```

Users should NOT be required to interact with raw ComfyUI for normal operation.

---

# 42. ENGINE ABSTRACTION

The platform must support multiple execution engines.

Example:

```text
Engine Layer

ComfyUI
Diffusers
Custom Python Engine
Future Engines
```

The core platform must not be tightly coupled to ComfyUI.

---

# 43. STORAGE ARCHITECTURE

Use:

```text
Database
+
File/Object Storage
```

The relational database stores metadata.

Large media files must NOT be stored directly inside normal relational database records.

---

# 44. STORAGE TYPES

The architecture should support:

```text
Local Filesystem
NFS
SMB
S3
MinIO
Object Storage
```

Storage providers must be abstracted.

---

# 45. FILE ORGANIZATION

A possible structure:

```text
storage/
    projects/
        PROJECT-001/
            assets/
            characters/
            locations/
            sequences/
            shots/
            images/
            videos/
            exports/

    shared/
        characters/
        locations/
        assets/

    models/

    workflows/

    logs/
```

Do not hard-code storage paths throughout the application.

Use a storage abstraction layer.

---

# 46. VERSIONING

Generated outputs must be versioned.

Example:

```text
SHOT-001
    v001
    v002
    v003
```

Every generation must preserve its metadata.

At minimum:

```text
Model
Model Version
Workflow
Workflow Version
Prompt
Negative Prompt
Seed
Parameters
Reference Assets
GPU
Node
Timestamp
Generation Duration
```

---

# 47. REPRODUCIBILITY

The platform must preserve enough metadata to reproduce a generation as accurately as technically possible.

A generation should be associated with:

```text
Exact Model
Exact Model Version
Exact Workflow
Exact Workflow Version
Prompt
Negative Prompt
Seed
Parameters
Reference Assets
Input Assets
Engine Version
Runtime Information
```

---

# 48. LOGGING

Centralized logging is required.

Each log record should include:

```text
Timestamp
Level
Service
Node
GPU
Job
Project
Model
Workflow
Message
Stack Trace
```

Log levels:

```text
DEBUG
INFO
WARNING
ERROR
CRITICAL
```

---

# 49. LOG CENTER

The web interface must provide:

* real-time logs
* filtering
* search
* log levels
* service filtering
* node filtering
* GPU filtering
* job filtering
* project filtering
* time filtering

---

# 50. MONITORING

Monitor:

```text
CPU
RAM
Disk
Network
GPU Utilization
GPU Memory
GPU Temperature
GPU Power
Node Health
Worker Health
Queue Size
Job Throughput
Job Failure Rate
Storage Usage
```

---

# 51. HEALTH CHECKS

Every service and worker must provide health information.

Node states:

```text
HEALTHY
DEGRADED
OFFLINE
UNHEALTHY
MAINTENANCE
```

GPU states:

```text
AVAILABLE
BUSY
RESERVED
ERROR
OFFLINE
```

---

# 52. AUTOMATIC RECOVERY

The system must support automatic recovery.

Example:

```text
Worker crashes
    ↓
Heartbeat lost
    ↓
Worker marked offline
    ↓
Running jobs evaluated
    ↓
Retryable jobs requeued
    ↓
Compatible GPU selected
    ↓
Job resumed/restarted
```

---

# 53. RESOURCE LOCKING

When a GPU is assigned:

```text
AVAILABLE
    ↓
RESERVED
    ↓
BUSY
```

When complete:

```text
BUSY
    ↓
AVAILABLE
```

The scheduler must prevent double allocation.

---

# 54. PRIORITY

Jobs must support:

```text
LOW
NORMAL
HIGH
URGENT
```

The scheduling system must be priority-aware.

---

# 55. JOB CONTROL

Users must be able to:

```text
Pause
Resume
Cancel
Retry
Change Priority
Requeue
Move
Inspect
```

where technically supported.

---

# 56. API-FIRST DESIGN

Every important operation available through the UI must also be available through an API.

Examples:

```text
/api/projects
/api/projects/{id}

/api/assets
/api/characters
/api/locations

/api/shots
/api/images
/api/videos

/api/jobs
/api/jobs/{id}

/api/nodes
/api/gpus

/api/models
/api/workflows

/api/logs
/api/metrics
```

Use a consistent REST or equivalent API architecture.

---

# 57. API DOCUMENTATION

API documentation must be automatically generated where possible.

Use an industry-standard schema such as OpenAPI.

---

# 58. AUTHENTICATION

The platform must support:

```text
Users
Roles
Permissions
API Tokens
Sessions
```

---

# 59. ROLES

At minimum:

```text
ADMIN
OPERATOR
ARTIST
VIEWER
```

Permissions must be granular enough to control:

* project access
* asset management
* generation
* model management
* GPU management
* worker management
* system configuration

---

# 60. SECURITY

Security must be designed from the beginning.

Protect against:

* command injection
* path traversal
* arbitrary file execution
* unauthorized GPU access
* unauthorized model execution
* credential leakage
* unsafe API access
* malicious workflow execution
* privilege escalation

Secrets must never be hard-coded.

Use environment variables or a proper secret management mechanism.

---

# 61. AUDIT LOG

Sensitive operations must be audited.

Record:

```text
User
Action
Resource
Previous Value
New Value
Timestamp
IP
```

---

# 62. DATABASE

Use a production-grade relational database.

PostgreSQL is preferred unless a strong technical reason exists for another choice.

The schema must be:

* normalized
* extensible
* indexed
* migration-based
* version-controlled

---

# 63. CORE DATABASE ENTITIES

At minimum:

```text
User
Role
Permission

Project
Sequence
Shot

Asset
Character
Location

ReferenceMedia
GeneratedMedia

GPUNode
GPUDevice
GPUCapability

Job
JobAttempt

Model
ModelVersion
ModelInstallation

Workflow
WorkflowVersion

StorageObject

LogEntry
AuditEntry
Metric
```

---

# 64. QUEUE SYSTEM

Use a production-grade job/message queue.

The queue layer must be abstract enough to allow future replacement.

The queue must support:

* priorities
* retries
* delayed jobs
* cancellation
* worker assignment
* job status
* failure handling

---

# 65. FRONTEND ARCHITECTURE

Use a modern component-based frontend.

Possible technologies:

```text
React
Next.js
TypeScript
```

or another production-grade equivalent.

The final technology choice must be justified.

---

# 66. BACKEND ARCHITECTURE

A Python backend is preferred unless another choice has a strong technical advantage.

Possible technologies:

```text
FastAPI
Django
```

The architecture should separate:

```text
API
Domain Logic
Persistence
Scheduling
Workers
Integrations
Infrastructure
```

---

# 67. SERVICE BOUNDARIES

Avoid creating unnecessary microservices.

Start with a clean modular architecture.

Services/components should be separated logically:

```text
API
Orchestrator
Scheduler
Worker
Model Manager
Workflow Manager
Storage
Monitoring
```

Only split into independent deployable services where there is a real operational benefit.

---

# 68. CONTAINERIZATION

The platform should preferably support Docker-based deployment.

Potential components:

```text
Frontend
Backend
Orchestrator
Scheduler
Worker
Database
Queue
Monitoring
Storage
```

GPU workers must use the appropriate NVIDIA container/runtime mechanisms when required.

---

# 69. SINGLE-NODE DEPLOYMENT

The first deployment must work on a single server.

Example:

```text
Single Server

Frontend
Backend
Database
Queue
Orchestrator
Scheduler
Worker
GPU(s)
Storage
```

---

# 70. MULTI-NODE DEPLOYMENT

The architecture must later support:

```text
Controller Node
        |
        +---- Worker Node 01
        |
        +---- Worker Node 02
        |
        +---- Worker Node 03
        |
        +---- Worker Node N
```

Adding a node must not require rewriting the core system.

---

# 71. NODE REGISTRATION

New worker:

```text
Install Agent
    ↓
Connect to Controller
    ↓
Authenticate
    ↓
Discover Hardware
    ↓
Discover GPUs
    ↓
Detect Capabilities
    ↓
Register
    ↓
Heartbeat
    ↓
Ready
```

---

# 72. HEARTBEAT

Workers must periodically report health.

If heartbeat is lost:

```text
Worker → OFFLINE
```

The controller must evaluate affected jobs automatically.

---

# 73. MODEL DISTRIBUTION

The platform should eventually support:

```text
Download
Verify
Install
Cache
Update
Rollback
Remove
```

Model distribution must be aware of:

* disk space
* network availability
* checksum
* compatibility
* GPU requirements

---

# 74. REMOTE MODEL SOURCES

The model system should support configurable model sources such as:

* Hugging Face
* Git repositories
* direct model URLs
* internal model storage
* private model registries

Do not hard-code one external provider.

---

# 75. NETWORK FAILURE HANDLING

Model downloads and remote operations must support:

* retries
* resumable downloads where possible
* checksum validation
* failure reporting
* configurable mirrors

---

# 76. STORAGE SCALABILITY

Do not assume that local disk is the only storage backend.

The abstraction must allow:

```text
Local
NFS
SMB
MinIO
S3
Other Object Storage
```

---

# 77. BACKUP

The system must support backup policies for:

```text
Database
Project Metadata
Configuration
Workflow Definitions
Model Registry
Important Assets
```

Large generated media should have configurable backup policies.

---

# 78. SEARCH

Provide global search across:

```text
Projects
Characters
Locations
Assets
Shots
Jobs
Models
Workflows
Logs
```

---

# 79. TAGGING

Entities should support tags.

Examples:

```text
cinematic
historical
night
battle
character
close-up
wide-shot
sci-fi
advertisement
```

Tags must not be restricted to a predefined genre.

---

# 80. FUTURE AI AUTOMATION

The architecture must allow future automation such as:

```text
Story → Sequence Planning
Sequence → Shot Planning
Shot → Prompt Generation
Prompt → Model Selection
Model → Workflow Selection
Workflow → GPU Selection
Generation → Quality Evaluation
Quality Evaluation → Automatic Retry
```

---

# 81. AUTOMATIC MODEL SELECTION

In the future a user may simply request:

> Generate a cinematic realistic image.

The platform should be able to select:

```text
Best Model
Best Model Version
Best Workflow
Best GPU
Best Resolution
```

based on the registry and policy.

Manual override must always remain available.

---

# 82. QUALITY EVALUATION

The system should be designed for future quality evaluation.

Potential metrics:

```text
Prompt Adherence
Image Quality
Video Quality
Character Consistency
Artifact Detection
Resolution
Motion Quality
Temporal Consistency
```

A quality score may determine whether a job should automatically retry.

---

# 83. CHARACTER CONSISTENCY

The platform should eventually support mechanisms for maintaining consistent characters across shots.

Character references must be reusable.

Generation metadata must preserve which references were used.

---

# 84. PROJECT STORY STRUCTURE

Projects should support:

```text
Project
    ↓
Sequences
    ↓
Shots
    ↓
Generations
    ↓
Final Outputs
```

Example:

```text
Project
 ├── Sequence 01
 │    ├── Shot 001
 │    ├── Shot 002
 │    └── Shot 003
 │
 ├── Sequence 02
 │    ├── Shot 004
 │    └── Shot 005
 │
 └── Sequence 03
```

---

# 85. MEDIA PIPELINE

A typical pipeline should be:

```text
Project
    ↓
Characters / Locations / Assets
    ↓
Shot Definition
    ↓
Reference Image
    ↓
Image Generation
    ↓
Image Selection
    ↓
Image-to-Video
    ↓
Video Selection
    ↓
Upscaling
    ↓
Post Processing
    ↓
Final Export
```

Every stage must preserve metadata.

---

# 86. MEDIA PREVIEW

The UI should provide previews for:

* images
* videos
* references
* generated outputs

Users should be able to compare versions.

---

# 87. GENERATION HISTORY

Every generation must remain traceable.

Example:

```text
Shot 034

Generation 001
    Model A
    Seed 123
    Failed

Generation 002
    Model A
    Seed 456
    Accepted

Generation 003
    Model B
    Seed 789
    Rejected
```

---

# 88. FAVORITES / APPROVAL

Users should be able to mark outputs:

```text
PENDING
SELECTED
REJECTED
FINAL
ARCHIVED
```

This should be usable for both images and videos.

---

# 89. PROJECT EXPORT

A project should eventually be exportable.

Export should include:

```text
Project Metadata
Assets
Selected Images
Selected Videos
Shot List
Generation Metadata
Workflow Metadata
```

---

# 90. OBSERVABILITY

The system must provide enough information to answer:

* What is running?
* Where is it running?
* Why is it running there?
* Which model is being used?
* Which workflow is being used?
* How much VRAM is being used?
* Why did a job fail?
* How long did it take?
* Which GPU produced the output?
* Can it be reproduced?
* Which version produced it?

---

# 91. PERFORMANCE

Optimize for:

* GPU utilization
* model reuse
* model caching
* queue throughput
* low scheduling latency
* efficient storage
* efficient network transfers

Do not optimize prematurely.

Measure first.

---

# 92. TESTING

Required test categories:

```text
Unit Tests
Integration Tests
API Tests
Database Tests
Scheduler Tests
Queue Tests
Worker Tests
GPU Discovery Tests
Capability Tests
Model Registry Tests
Workflow Tests
Storage Tests
Frontend Tests
End-to-End Tests
Failure Recovery Tests
```

---

# 93. FAILURE TESTING

Explicitly test:

```text
GPU failure
Worker crash
Node offline
Network failure
Model download failure
CUDA error
Out-of-memory
Disk full
Database failure
Queue failure
Invalid workflow
Invalid model
Job cancellation
```

---

# 94. DOCUMENTATION

The project must include:

```text
README.md

docs/
    architecture.md
    installation.md
    deployment.md
    development.md
    api.md
    gpu-discovery.md
    workers.md
    scheduler.md
    models.md
    workflows.md
    projects.md
    assets.md
    shots.md
    storage.md
    security.md
    monitoring.md
    troubleshooting.md
```

Documentation must be updated as the architecture evolves.

---

# 95. INSTALLATION

Provide an installation process that can configure:

```text
Dependencies
Container Runtime
NVIDIA Runtime
Database
Queue
Backend
Frontend
Worker
Storage
GPU Detection
Configuration
```

Installation must be reproducible.

---

# 96. CONFIGURATION

Configuration must be externalized.

Use:

```text
Environment Variables
Configuration Files
Secrets
```

Do not hard-code:

* credentials
* API keys
* storage paths
* database passwords
* node addresses
* model paths

---

# 97. CI/CD

The project should support:

```text
Linting
Formatting
Static Analysis
Unit Tests
Integration Tests
Build
Container Build
Security Checks
Deployment
```

Use Git-based version control.

---

# 98. DEVELOPMENT PHASES

## Phase 1 — Foundation

Build:

```text
Repository
Backend
Frontend
Database
Authentication
Basic API
Configuration System
```

---

## Phase 2 — GPU Infrastructure

Build:

```text
Worker Agent
Node Discovery
GPU Discovery
GPU Metrics
Heartbeat
Health Monitoring
Node Registration
```

---

## Phase 3 — Job System

Build:

```text
Job Model
Queue
Scheduler
GPU Allocation
Worker Execution
Progress Tracking
Retry
Cancellation
Logs
```

---

## Phase 4 — Model System

Build:

```text
Model Registry
Model Versions
Model Installation
Model Validation
Model Cache
Compatibility Detection
```

---

## Phase 5 — Workflow System

Build:

```text
Workflow Registry
Workflow Versions
Engine Adapter
ComfyUI Adapter
Workflow Execution
```

---

## Phase 6 — Asset System

Build:

```text
Projects
Characters
Locations
Assets
References
Versioning
Search
Tags
```

---

## Phase 7 — Image Generation

Build:

```text
Image Generator
Prompt System
Reference System
Batch Generation
Shot Generator
Image History
```

---

## Phase 8 — Video Generation

Build:

```text
Video Generator
Image-to-Video
Text-to-Video
Video Queue
Video Workflows
Video History
```

---

## Phase 9 — Advanced Orchestration

Build:

```text
Multi-node
Advanced Scheduling
Multi-GPU Support
Automatic Model Selection
Model Cache Optimization
Automatic Retry
Quality Evaluation
```

---

## Phase 10 — Production Hardening

Build:

```text
Security
Monitoring
Backup
Performance Optimization
Testing
Documentation
Deployment
Recovery
```

---

# 99. MVP REQUIREMENTS

The first functional MVP must include at least:

```text
GPU Discovery
Node Discovery
GPU Dashboard
Projects
Characters
Locations
Assets
Shots
Job Queue
Scheduler
Worker
Model Registry
Workflow Registry
ComfyUI Integration
Image Generation
Logs
Storage
Authentication
```

Video generation may follow immediately after the MVP foundation is stable.

---

# 100. DEVELOPMENT RULES

These rules are mandatory.

## Rule 1

Do not hard-code GPUs.

## Rule 2

Do not hard-code projects.

## Rule 3

Do not hard-code models.

## Rule 4

Do not hard-code workflows.

## Rule 5

Do not hard-code storage paths.

## Rule 6

Do not assume one GPU.

## Rule 7

Do not assume one node.

## Rule 8

Do not assume one project.

## Rule 9

Do not assume ComfyUI is the only inference engine.

## Rule 10

Do not assume every model supports multi-GPU.

## Rule 11

Do not store large media directly inside the relational database.

## Rule 12

Do not put secrets into source code.

## Rule 13

Do not build the system as a temporary demo.

## Rule 14

Do not sacrifice the architecture simply to make the MVP appear faster.

---

# 101. ARCHITECTURAL DECISION PROCESS

Before making major architectural decisions:

1. Identify the problem.
2. List available options.
3. Compare trade-offs.
4. Evaluate scalability.
5. Evaluate operational complexity.
6. Evaluate performance.
7. Evaluate maintainability.
8. Select the recommended approach.
9. Document the decision.

Use Architecture Decision Records (ADRs) where appropriate.

---

# 102. IMPLEMENTATION PROCESS

For every development phase:

```text
Design
    ↓
Implementation
    ↓
Unit Tests
    ↓
Integration Tests
    ↓
Validation
    ↓
Documentation
    ↓
Commit
```

Do not move forward while leaving major broken foundations behind.

---

# 103. FIRST RESPONSE REQUIREMENT

When this specification is provided to an AI coding agent or development team, DO NOT immediately generate the entire codebase.

The first response must contain:

1. Final Architecture
2. Technology Stack
3. Repository Structure
4. Service Architecture
5. Database Design
6. GPU/Worker Architecture
7. Job Lifecycle
8. Scheduler Architecture
9. Model Registry Architecture
10. Workflow Architecture
11. Storage Architecture
12. Security Architecture
13. Observability Architecture
14. MVP Roadmap
15. Risks and Technical Challenges
16. Phase 1 Implementation Plan

Then wait for approval before implementing Phase 1.

---

# 104. EXPECTED FINAL USER EXPERIENCE

The final system should allow a user to:

```text
Create Project
        ↓
Create Characters
        ↓
Create Locations
        ↓
Upload References
        ↓
Create Sequence
        ↓
Create Shots
        ↓
Generate Images
        ↓
Select Images
        ↓
Generate Videos
        ↓
Select Videos
        ↓
Upscale
        ↓
Export
```

while the infrastructure automatically handles:

```text
GPU Discovery
Model Selection
Workflow Selection
GPU Allocation
Queueing
Scheduling
Execution
Monitoring
Logging
Retry
Storage
Versioning
```

---

# 105. FINAL PRODUCT DEFINITION

The final product is not simply:

* a ComfyUI wrapper
* a GPU dashboard
* an image generator
* a video generator
* a job queue
* a model downloader

It is a unified:

# AI MEDIA OPERATING SYSTEM

that provides a control plane for AI-powered media production across heterogeneous GPU infrastructure.

The system must allow the user to start with:

```text
5 × GTX 1080 Ti
```

and later grow into:

```text
multiple servers
multiple GPU generations
multiple models
multiple engines
multiple projects
multiple users
```

without requiring a fundamental rewrite.

---

# 106. START HERE

Before writing implementation code, produce the following documents/designs:

```text
01 — System Architecture
02 — Technology Stack
03 — Repository Structure
04 — Service Boundaries
05 — Database ERD
06 — Database Schema
07 — GPU Discovery Protocol
08 — Worker Protocol
09 — Job Lifecycle
10 — Scheduler Design
11 — Model Registry Design
12 — Workflow Registry Design
13 — Asset/Project/Shot Data Model
14 — Storage Architecture
15 — API Specification
16 — Authentication/RBAC Design
17 — Observability Design
18 — Deployment Architecture
19 — MVP Roadmap
20 — Phase 1 Implementation Plan
```

Do not start by generating hundreds of unrelated files.

Start with the architecture and make every implementation decision traceable to the requirements above.

# END OF SPECIFICATION
