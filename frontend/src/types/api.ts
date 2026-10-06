export interface User {
  id: string;
  username: string;
  email: string | null;
  display_name: string;
  is_active: boolean;
  roles: Role[];
  created_at: string;
  last_login_at: string | null;
}

export interface Role {
  id: string;
  code: string;
  name: string;
  description: string | null;
}

export interface ApiToken {
  token: string;
  prefix: string;
  expires_at: string;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  csrf_token: string;
  expires_at: string;
  user: User;
}

export interface ApiTokenCreate {
  name: string;
  scopes: string[];
  ttl_days: number;
}

export interface ApiTokenOut {
  token: string;
  prefix: string;
  expires_at: string;
}

export interface Project {
  id: string;
  name: string;
  description: string | null;
  owner_id: string;
  created_at: string;
  updated_at: string;
  sequences_count: number;
  shots_count: number;
}

export interface ProjectCreate {
  name: string;
  description?: string;
}

export interface Sequence {
  id: string;
  project_id: string;
  name: string;
  description: string | null;
  order_index: number;
  created_at: string;
  updated_at: string;
  shots_count: number;
}

export interface SequenceCreate {
  name: string;
  description?: string;
  order_index?: number;
}

export interface Shot {
  id: string;
  sequence_id: string;
  name: string;
  description: string | null;
  order_index: number;
  camera_config: Record<string, unknown> | null;
  lighting_config: Record<string, unknown> | null;
  duration_seconds: number | null;
  created_at: string;
  updated_at: string;
  generations_count: number;
}

export interface ShotCreate {
  name: string;
  description?: string;
  order_index?: number;
  camera_config?: Record<string, unknown>;
  lighting_config?: Record<string, unknown>;
  duration_seconds?: number;
}

export interface ShotBatchCreate {
  shots: ShotCreate[];
}

export interface Asset {
  id: string;
  project_id: string | null;
  name: string;
  asset_type: string;
  description: string | null;
  tags: string[];
  created_at: string;
  updated_at: string;
  versions_count: number;
}

export interface AssetCreate {
  name: string;
  asset_type: string;
  description?: string;
  tags?: string[];
  project_id?: string;
}

export interface AssetVersion {
  id: string;
  asset_id: string;
  version_number: number;
  storage_key: string;
  mime_type: string;
  size_bytes: number;
  checksum: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

/** Matches JobOut as returned by GET /jobs. */
export interface Job {
  id: string;
  project_id: string | null;
  shot_id: string | null;
  job_type: string;
  prompt: string | null;
  negative_prompt: string | null;
  params: Record<string, unknown>;
  priority: string;
  max_retries: number;
  status: JobStatus;
  progress_percent: number;
  progress_message: string | null;
  progress_step: number | null;
  progress_total: number | null;
  error: Record<string, unknown> | null;
  retry_count: number;
  worker_id: string | null;
  cancel_requested: boolean;
  duration_seconds: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  gpu_device_id: string | null;
  node_id: string | null;
}

/** Matches JobEventOut. */
export interface JobEvent {
  seq: number;
  event_type: string;
  payload: Record<string, unknown> | null;
  ts: string;
}

export type JobStatus =
  | 'CREATED'
  | 'QUEUED'
  | 'RESERVED'
  | 'STARTING'
  | 'RUNNING'
  | 'PAUSED'
  | 'RETRYING'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED'
  | 'UNRESERVABLE';

export interface JobCreate {
  project_id?: string;
  sequence_id?: string;
  shot_id?: string;
  job_type: string;
  model_version_id?: string;
  workflow_version_id?: string;
  prompt?: string;
  negative_prompt?: string;
  params?: Record<string, unknown>;
  required_vram_mb?: number;
  required_capabilities?: string[];
  preferred_gpu_ids?: string[];
  preferred_node_ids?: string[];
  priority?: string;
  policy?: string;
  max_retries?: number;
  queue?: string;
}

export interface JobOut {
  id: string;
  project_id: string | null;
  sequence_id: string | null;
  shot_id: string | null;
  job_type: string;
  model_version_id: string | null;
  workflow_version_id: string | null;
  prompt: string | null;
  negative_prompt: string | null;
  params: Record<string, unknown>;
  required_vram_mb: number;
  required_capabilities: string[];
  priority: string;
  policy: string | null;
  max_retries: number;
  queue: string | null;
  status: JobStatus;
  progress_percent: number;
  error: Record<string, unknown> | null;
  retry_count: number;
  created_by: string;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  gpu_device_id: string | null;
  node_id: string | null;
}

/** ORM shape of a job event; the API exposes JobEvent instead. */
export interface JobEventRow {
  id: string;
  job_id: string;
  attempt_id: string | null;
  seq: number;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface Worker {
  id: string;
  name: string;
  node_id: string | null;
  status: string;
  version: string | null;
  platform: string | null;
  capabilities: Record<string, unknown>;
  active_job_count: number;
  last_heartbeat_at: string | null;
  registered_at: string | null;
}

export interface WorkerRegistrationRequest {
  name: string;
  version?: string;
  platform?: string;
  capabilities?: Record<string, unknown>;
  node_key?: string;
}

export interface WorkerRegistrationOut {
  worker_id: string;
  credential: string;
  prefix: string;
  expires_at: string;
}

export interface WorkerCreatedOut {
  worker_id: string;
  registration_token: string;
  expires_at: string;
}

export interface HeartbeatRequest {
  status?: string;
  active_job_count?: number;
  claim_ids?: string[];
  gpus?: Array<Record<string, unknown>>;
  cache_state?: Record<string, unknown> | null;
}

export interface HeartbeatOut {
  worker_id: string;
  server_time: string;
  lease_ttl_seconds: number;
  heartbeat_interval_seconds: number;
  renewed_claim_ids: string[];
  lost_claim_ids: string[];
}

export interface ClaimRequest {
  job_id?: string;
  capabilities?: Record<string, unknown>;
}

export interface ClaimOut {
  job_id: string;
  attempt_id: string;
  attempt_no: number;
  claim_id: string;
  lease_expires_at: string;
  lease_ttl_seconds: number;
  heartbeat_interval_seconds: number;
  gpu_device_id: string;
  gpu_backend_device_id: string;
  node_id: string;
  node_key: string | null;
  backend: string | null;
  job_type: string;
  params: Record<string, unknown>;
  prompt: string | null;
  negative_prompt: string | null;
  model_version_id: string | null;
  workflow_version_id: string | null;
  required_vram_mb: number | null;
}

export interface ProgressRequest {
  step?: number;
  total?: number;
  percent?: number;
  message?: string;
}

export interface ProgressOut {
  job_id: string;
  status: string;
  progress_percent: number;
  started: boolean;
}

export interface OutputDescriptor {
  kind: 'file' | 'url';
  mime_type: string;
  filename?: string;
  url?: string;
  content_base64?: string;
  asset_role?: string;
  metadata?: Record<string, unknown>;
}

export interface CompleteRequest {
  outputs: OutputDescriptor[];
  runtime_info?: Record<string, unknown>;
  duration_seconds?: number;
}

export interface FailRequest {
  failure_class?: string;
  message?: string;
  error?: Record<string, unknown>;
  retryable?: boolean;
}

export interface TerminalOut {
  job_id: string;
  status: string;
  attempt_id: string | null;
  artifacts: number;
  release_requested: boolean;
}

export interface GpuDevice {
  id: string;
  node_id: string;
  uuid: string;
  index: number;
  name: string;
  backend_device_id: string | null;
  status: string;
  health: string;
  vram_total_mb: number;
  vram_free_mb: number;
  compute_capability: string | null;
  driver_version: string | null;
  utilization_percent: number;
  temperature_c: number | null;
  power_w: number | null;
  memory_used_mb: number;
  capabilities: GpuCapability[];
}

export interface GpuCapability {
  id: string;
  gpu_device_id: string;
  capability: string;
  value: Record<string, unknown> | null;
  source: string;
  checked_at: string | null;
}

export interface GpuNode {
  id: string;
  name: string;
  hostname: string | null;
  status: string;
  labels: Record<string, unknown>;
  last_heartbeat_at: string | null;
  devices_count: number;
}

export interface GpuReservation {
  id: string;
  job_id: string;
  worker_id: string | null;
  gpu_device_id: string;
  node_id: string;
  state: string;
  policy: string | null;
  scores: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface Model {
  id: string;
  key: string;
  name: string;
  family: string;
  job_type: string;
  precision: string | null;
  required_capabilities: string[];
  min_vram_mb: number;
  est_seconds: number;
  source: string;
  notes: string | null;
}

export interface Workflow {
  key: string;
  name: string;
  version: string | null;
  node_count: number;
  required_capabilities: string[];
  min_vram_mb: number;
  model_keys: string[];
  source: string;
}

export interface RequirementsResponse {
  required_vram_mb: number;
  required_capabilities: string[];
  model: string | null;
  workflow: string | null;
}

export interface HealthResponse {
  status: 'healthy' | 'degraded' | 'unhealthy';
  checks: Record<string, { status: string; message?: string }>;
}

export interface ReadinessResponse {
  ready: boolean;
  checks: Record<string, { ready: boolean; message?: string }>;
}

export interface MetricsResponse {
  jobs: {
    queued: number;
    running: number;
    completed: number;
    failed: number;
    retrying: number;
  };
  gpus: {
    total: number;
    available: number;
    reserved: number;
    busy: number;
    unhealthy: number;
  };
  workers: {
    total: number;
    idle: number;
    busy: number;
    offline: number;
  };
  queue: {
    depth: number;
    oldest_job_age_seconds: number | null;
  };
}

export interface StorageObject {
  id: string;
  backend: string;
  bucket: string;
  key: string;
  size_bytes: number;
  mime_type: string;
  checksum: string | null;
  owner_type: string;
  owner_id: string | null;
  created_at: string;
}

/** Matches GeneratedMediaOut. */
export interface GeneratedMedia {
  id: string;
  shot_id: string | null;
  project_id: string | null;
  job_id: string | null;
  media_type: string;
  asset_role: string;
  current_version_no: number;
  approval_status: string;
  prompt: string | null;
  reproducibility: Record<string, unknown>;
}

/** Matches MediaVersionOut. */
export interface MediaVersion {
  version_no: number;
  storage_key: string;
  mime_type: string;
  size_bytes: number;
  checksum: string | null;
}

/** ORM shape of a media version; the API exposes MediaVersion instead. */
export interface GeneratedMediaVersion {
  id: string;
  media_id: string;
  version_no: number;
  storage_object_id: string;
  prompt: string | null;
  parameters: Record<string, unknown>;
  reproducibility: Record<string, unknown>;
  created_by: string | null;
  created_at: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface ApiError {
  code: string;
  message: string;
  request_id: string;
  errors?: Array<{
    type: string;
    loc: (string | number)[];
    msg: string;
    input?: unknown;
  }>;
}
