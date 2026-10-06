import axios, { AxiosError, AxiosInstance, InternalAxiosRequestConfig } from 'axios';
import { ApiError } from '../types/api';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1';

/**
 * List endpoints take `limit`/`offset`, never `page`/`page_size`; only some of
 * them report a `total`. `Page<T>` mirrors the three that do.
 */
export interface Page<T> {
  items: T[];
  total: number;
}

export interface ListParams {
  limit?: number;
  offset?: number;
}

function unwrap<T>(payload: unknown): T[] {
  // Bare arrays (most endpoints) and {items: [...]} are both in use.
  if (Array.isArray(payload)) return payload as T[];
  const items = (payload as { items?: T[] } | null)?.items;
  return Array.isArray(items) ? items : [];
}

class ApiClient {
  private client: AxiosInstance;
  private csrfToken: string | null = null;
  private accessToken: string | null = null;

  constructor() {
    this.client = axios.create({
      baseURL: API_BASE_URL,
      headers: { 'Content-Type': 'application/json' },
      withCredentials: true,
    });

    this.client.interceptors.request.use(
      (config: InternalAxiosRequestConfig) => {
        const mutating = ['post', 'put', 'patch', 'delete'];
        if (mutating.includes(config.method || '') && this.csrfToken) {
          config.headers['X-CSRF-Token'] = this.csrfToken;
        }
        if (this.accessToken) {
          config.headers.Authorization = `Bearer ${this.accessToken}`;
        }
        return config;
      },
      (error) => Promise.reject(error),
    );

    this.client.interceptors.response.use(
      (response) => response,
      (error: AxiosError<ApiError>) => {
        if (error.response?.status === 401 && window.location.pathname !== '/login') {
          this.clearAuth();
          window.location.href = '/login';
        }
        return Promise.reject(error);
      },
    );
  }

  setCsrfToken(token: string | null) {
    this.csrfToken = token;
  }

  setAccessToken(token: string | null) {
    this.accessToken = token;
  }

  clearAuth() {
    this.csrfToken = null;
    this.accessToken = null;
  }

  private get<T>(path: string, params?: Record<string, unknown> | ListParams) {
    return this.client.get<T>(path, { params });
  }

  private post<T>(path: string, body?: unknown, headers?: Record<string, string>) {
    return this.client.post<T>(path, body, { headers });
  }

  // --- auth ---------------------------------------------------------------
  login(username: string, password: string) {
    return this.post<{ csrf_token: string; expires_at: string; user: unknown }>(
      '/auth/login',
      { username, password },
    );
  }

  logout() {
    return this.post('/auth/logout');
  }

  getCurrentUser<T>() {
    return this.get<T>('/auth/me').then((r) => r.data);
  }

  createToken(name: string, scopes: string[], ttlDays: number) {
    return this
      .post<{ token: string; prefix: string; expires_at: string }>('/auth/tokens', {
        name,
        scopes,
        ttl_days: ttlDays,
      })
      .then((r) => r.data);
  }

  listTokens<T>() {
    return this.get<unknown>('/auth/tokens').then((r) => unwrap<T>(r.data));
  }

  revokeToken(tokenId: string) {
    return this.client.delete(`/auth/tokens/${tokenId}`);
  }

  listUsers<T>(params?: ListParams) {
    return this.get<unknown>('/users', params).then((r) => unwrap<T>(r.data));
  }

  createUser(data: {
    username: string;
    password: string;
    email?: string;
    display_name?: string;
    roles?: string[];
  }) {
    return this.post('/users', data).then((r) => r.data);
  }

  // --- jobs ----------------------------------------------------------------
  listJobs<T>(params?: ListParams & { status?: string; job_type?: string; project_id?: string }) {
    return this
      .get<Page<unknown>>('/jobs', params as Record<string, unknown>)
      .then((r) => ({ items: unwrap<T>(r.data), total: r.data.total ?? 0 }));
  }

  createJob<T>(data: unknown) {
    return this.post<T>('/jobs', data).then((r) => r.data);
  }

  getJob<T>(jobId: string) {
    return this.get<T>(`/jobs/${jobId}`).then((r) => r.data);
  }

  getJobEvents<T>(jobId: string, params?: { from_seq?: number; limit?: number }) {
    return this.get<unknown>(`/jobs/${jobId}/events`, params).then((r) => unwrap<T>(r.data));
  }

  getLegalTransitions<T>(jobId: string) {
    return this.get<T>(`/jobs/${jobId}/legal-transitions`).then((r) => r.data);
  }

  cancelJob(jobId: string) {
    return this.post(`/jobs/${jobId}/cancel`).then((r) => r.data);
  }

  pauseJob(jobId: string) {
    return this.post(`/jobs/${jobId}/pause`).then((r) => r.data);
  }

  resumeJob(jobId: string) {
    return this.post(`/jobs/${jobId}/resume`).then((r) => r.data);
  }

  retryJob(jobId: string) {
    return this.post(`/jobs/${jobId}/retry`).then((r) => r.data);
  }

  setJobPriority(jobId: string, priority: string) {
    return this.post(`/jobs/${jobId}/priority`, { priority }).then((r) => r.data);
  }

  // --- workers -------------------------------------------------------------
  registerWorker(data: unknown, registrationToken: string) {
    return this
      .post<{ worker_id: string; credential: string; prefix: string; expires_at: string }>(
        '/workers/register',
        data,
        { 'X-Registration-Token': registrationToken },
      )
      .then((r) => r.data);
  }

  listWorkers<T>(params?: ListParams & { status?: string }) {
    return this
      .get<Page<unknown>>('/workers', params as Record<string, unknown>)
      .then((r) => ({ items: unwrap<T>(r.data), total: r.data.total ?? 0 }));
  }

  getWorker<T>(workerId: string) {
    return this.get<T>(`/workers/${workerId}`).then((r) => r.data);
  }

  // --- GPUs ----------------------------------------------------------------
  listGpus<T>(params?: ListParams & { node_id?: string; status?: string; health?: string }) {
    return this.get<unknown>('/gpus', params as Record<string, unknown>).then((r) => unwrap<T>(r.data));
  }

  getGpu<T>(gpuId: string) {
    return this.get<T>(`/gpus/devices/${gpuId}`).then((r) => r.data);
  }

  listNodes<T>(params?: { status?: string }) {
    return this.get<unknown>('/gpus/nodes', params).then((r) => unwrap<T>(r.data));
  }

  getNode<T>(nodeId: string) {
    return this.get<T>(`/gpus/nodes/${nodeId}`).then((r) => r.data);
  }

  listReservations<T>(params?: { job_id?: string; state?: string }) {
    return this.get<unknown>('/gpus/reservations', params).then((r) => unwrap<T>(r.data));
  }

  // --- models & workflows ---------------------------------------------------
  listModels<T>(params?: { job_type?: string; status?: string }) {
    return this.get<unknown>('/models', params).then((r) => unwrap<T>(r.data));
  }

  listWorkflows<T>(params?: { job_type?: string }) {
    return this.get<unknown>('/models/workflows', params).then((r) => unwrap<T>(r.data));
  }

  getRequirements(modelKey?: string, workflowKey?: string) {
    return this
      .get<{ required_vram_mb: number; required_capabilities: string[] }>(
        '/models/requirements',
        { model_key: modelKey, workflow_key: workflowKey },
      )
      .then((r) => r.data);
  }

  // --- creative -------------------------------------------------------------
  listProjects<T>(params?: ListParams) {
    return this.get<unknown>('/projects', params).then((r) => unwrap<T>(r.data));
  }

  createProject<T>(data: { name: string; description?: string }) {
    return this.post<T>('/projects', data).then((r) => r.data);
  }

  getProject<T>(projectId: string) {
    return this.get<T>(`/projects/${projectId}`).then((r) => r.data);
  }

  listSequences<T>(projectId: string) {
    return this.get<unknown>(`/projects/${projectId}/sequences`).then((r) => unwrap<T>(r.data));
  }

  createSequence<T>(projectId: string, data: { name: string; description?: string }) {
    return this.post<T>(`/projects/${projectId}/sequences`, data).then((r) => r.data);
  }

  /** Shots hang off a sequence, not a project. */
  listShots<T>(sequenceId: string) {
    return this.get<unknown>(`/sequences/${sequenceId}/shots`).then((r) => unwrap<T>(r.data));
  }

  createShot<T>(sequenceId: string, data: unknown) {
    return this.post<T>(`/sequences/${sequenceId}/shots`, data).then((r) => r.data);
  }

  createShotsBatch<T>(sequenceId: string, shots: unknown[]) {
    return this.post<T>(`/sequences/${sequenceId}/shots/batch`, { shots }).then((r) => r.data);
  }

  listAssets<T>(params?: ListParams & { project_id?: string; asset_type?: string }) {
    return this.get<unknown>('/assets', params as Record<string, unknown>).then((r) => unwrap<T>(r.data));
  }

  createAsset<T>(data: unknown) {
    return this.post<T>('/assets', data).then((r) => r.data);
  }

  listGeneratedMedia<T>(params?: { job_id?: string; shot_id?: string; project_id?: string }) {
    return this.get<unknown>('/media', params).then((r) => unwrap<T>(r.data));
  }

  /** Authenticated download; axios must be told it is binary. */
  async downloadMedia(mediaId: string): Promise<Blob> {
    const response = await this.client.get(`/media/${mediaId}/current`, {
      responseType: 'blob',
    });
    return response.data as Blob;
  }

  /** Trigger a browser download for a media artifact. */
  async downloadMediaToFile(mediaId: string, filename: string): Promise<void> {
    const blob = await this.downloadMedia(mediaId);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  getMedia<T>(mediaId: string) {
    return this.get<T>(`/media/${mediaId}`).then((r) => r.data);
  }

  listMediaVersions<T>(mediaId: string) {
    return this
      .get<unknown>(`/media/${mediaId}/versions`)
      .then((r) => unwrap<T>(r.data));
  }

  // --- health ---------------------------------------------------------------
  health() {
    return this.get<unknown>('/health').then((r) => r.data);
  }

  readiness() {
    return this.get<unknown>('/ready').then((r) => r.data);
  }

  metrics() {
    return this.get<unknown>('/metrics').then((r) => r.data);
  }

  /** SSE endpoint; the caller owns the EventSource lifecycle. */
  streamUrl(params: Record<string, string> = {}) {
    const url = new URL(`${API_BASE_URL}/stream/jobs`, window.location.origin);
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
    return url.toString();
  }
}

export const api = new ApiClient();
export default api;
