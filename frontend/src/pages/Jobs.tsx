import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { Badge, Button, Input, Select, Spinner, Table } from '../components/UI';
import { useToast } from '../hooks/useToast';
import { extractErrorMessage } from '../hooks/useApi';
import { Job, JobStatus } from '../types/api';

const PAGE_SIZE = 20;

/** Terminal states accept no further action. */
const TERMINAL: JobStatus[] = ['COMPLETED', 'CANCELLED'];
/** Cancellable states, mirroring LEGAL_TRANSITIONS in the backend. */
const CANCELLABLE: JobStatus[] = [
  'QUEUED',
  'RESERVED',
  'STARTING',
  'RUNNING',
  'PAUSED',
  'RETRYING',
];

const STATUS_VARIANT: Record<string, 'success' | 'warning' | 'danger' | 'info' | 'neutral'> = {
  COMPLETED: 'success',
  RUNNING: 'info',
  STARTING: 'info',
  RESERVED: 'info',
  QUEUED: 'neutral',
  PAUSED: 'warning',
  RETRYING: 'warning',
  UNRESERVABLE: 'warning',
  FAILED: 'danger',
  CANCELLED: 'neutral',
};

const STATUS_OPTIONS: JobStatus[] = [
  'CREATED',
  'QUEUED',
  'RESERVED',
  'STARTING',
  'RUNNING',
  'PAUSED',
  'RETRYING',
  'COMPLETED',
  'FAILED',
  'CANCELLED',
  'UNRESERVABLE',
];

export function Jobs() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string>('');
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [pending, setPending] = useState<string | null>(null);
  const { showToast } = useToast();

  const fetchJobs = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.listJobs<Job>({
        limit: PAGE_SIZE,
        offset,
        status: status || undefined,
      });
      setJobs(res.items);
      setTotal(res.total);
    } catch (err: unknown) {
      setError(extractErrorMessage(err, 'Failed to load jobs'));
    } finally {
      setLoading(false);
    }
  }, [offset, status]);

  useEffect(() => {
    void fetchJobs();
  }, [fetchJobs]);

  const runAction = async (job: Job, action: 'cancel' | 'pause' | 'resume' | 'retry') => {
    setPending(job.id);
    try {
      if (action === 'cancel') await api.cancelJob(job.id);
      if (action === 'pause') await api.pauseJob(job.id);
      if (action === 'resume') await api.resumeJob(job.id);
      if (action === 'retry') await api.retryJob(job.id);
      showToast('success', `Job ${job.id.slice(0, 8)} ${action}d`);
      await fetchJobs();
    } catch (err: unknown) {
      showToast('error', extractErrorMessage(err, `Failed to ${action} job`));
    } finally {
      setPending(null);
    }
  };

  const filtered = jobs.filter((job) => {
    if (!search.trim()) return true;
    const needle = search.trim().toLowerCase();
    return job.id.toLowerCase().includes(needle) || job.job_type.toLowerCase().includes(needle);
  });

  const columns = [
    {
      key: 'id',
      header: 'ID',
      render: (job: Job) => (
        <Link to={`/jobs/${job.id}`} className="font-mono text-xs">
          {job.id.slice(0, 8)}
        </Link>
      ),
    },
    { key: 'job_type', header: 'Type', render: (job: Job) => job.job_type },
    {
      key: 'status',
      header: 'Status',
      render: (job: Job) => (
        <Badge variant={STATUS_VARIANT[job.status] ?? 'neutral'}>{job.status}</Badge>
      ),
    },
    {
      key: 'progress',
      header: 'Progress',
      render: (job: Job) =>
        job.status === 'RUNNING' || job.status === 'STARTING' ? (
          <div className="progress-bar" aria-label={`${job.progress_percent}% complete`}>
            <div className="progress-fill" style={{ width: `${job.progress_percent}%` }} />
            <span className="text-xs">{job.progress_percent}%</span>
          </div>
        ) : (
          <span className="text-muted">{job.progress_percent}%</span>
        ),
    },
    { key: 'priority', header: 'Priority' },
    {
      key: 'retries',
      header: 'Retries',
      render: (job: Job) => `${job.retry_count}/${job.max_retries}`,
    },
    {
      key: 'gpu',
      header: 'GPU',
      render: (job: Job) =>
        job.gpu_device_id ? (
          <span className="font-mono text-xs">{job.gpu_device_id.slice(0, 8)}</span>
        ) : (
          <span className="text-muted">—</span>
        ),
    },
    {
      key: 'created',
      header: 'Created',
      render: (job: Job) => (
        <span className="text-xs text-muted">{new Date(job.created_at).toLocaleString()}</span>
      ),
    },
  ];

  const rowActions = (job: Job) => (
    <div className="flex gap-2">
      {job.status === 'RUNNING' && (
        <Button size="sm" variant="secondary" disabled={pending === job.id} onClick={() => void runAction(job, 'pause')}>
          Pause
        </Button>
      )}
      {job.status === 'PAUSED' && (
        <Button size="sm" variant="secondary" disabled={pending === job.id} onClick={() => void runAction(job, 'resume')}>
          Resume
        </Button>
      )}
      {job.status === 'FAILED' && (
        <Button size="sm" disabled={pending === job.id} onClick={() => void runAction(job, 'retry')}>
          Retry
        </Button>
      )}
      {CANCELLABLE.includes(job.status) && !TERMINAL.includes(job.status) && (
        <Button
          size="sm"
          variant="danger"
          disabled={pending === job.id}
          onClick={() => void runAction(job, 'cancel')}
        >
          Cancel
        </Button>
      )}
    </div>
  );

  return (
    <div className="space-y-6">
      <div className="page-header">
        <div>
          <h1 className="page-title">Jobs</h1>
          <p className="page-description">Monitor and manage generation jobs</p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => void fetchJobs()}>
            Refresh
          </Button>
          <Link to="/generate/image">
            <Button>New image job</Button>
          </Link>
        </div>
      </div>

      <div className="flex gap-4 items-end">
        <div className="flex-1">
          <Input
            label="Search"
            placeholder="Filter by job ID or type"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div style={{ width: 200 }}>
          <Select
            label="Status"
            value={status}
            options={[
              { value: '', label: 'All statuses' },
              ...STATUS_OPTIONS.map((s) => ({ value: s, label: s })),
            ]}
            onChange={(e) => {
              setStatus(e.target.value);
              setOffset(0);
            }}
          />
        </div>
      </div>

      {error && <div className="alert alert-danger">{error}</div>}

      {loading ? (
        <div className="flex justify-center my-16">
          <Spinner size="lg" />
        </div>
      ) : (
        <Table<Job>
          columns={columns}
          data={filtered}
          keyExtractor={(job) => job.id}
          rowActions={rowActions}
          emptyMessage="No jobs match the current filters."
        />
      )}

      {!loading && total > 0 && (
        <div className="flex items-center justify-between">
          <p className="text-sm text-muted">
            Showing {offset + 1} to {Math.min(offset + PAGE_SIZE, total)} of {total}
          </p>
          <div className="flex gap-2">
            <Button
              variant="secondary"
              size="sm"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              Previous
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={offset + PAGE_SIZE >= total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
