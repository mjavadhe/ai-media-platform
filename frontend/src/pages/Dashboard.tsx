import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { Badge, Button, Card, EmptyState, Spinner } from '../components/UI';
import {
  Box,
  CheckCircle,
  Clock,
  Cpu,
  Database,
  FolderKanban,
  Image,
  Loader2,
  Play,
  Plus,
  TrendingUp,
  Video,
  XCircle,
} from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { useToast } from '../hooks/useToast';
import { extractErrorMessage } from '../hooks/useApi';
import { Job } from '../types/api';

/** `/metrics` is a flat snapshot; process gauges are empty until something records them. */
interface MetricsSnapshot {
  jobs_by_status?: Record<string, number>;
  queued_jobs?: number;
  sse_subscribers?: number;
  environment?: string;
  gpu_backend?: string;
  lease_ttl_seconds?: number;
}

interface DashboardState {
  metrics: MetricsSnapshot | null;
  recentJobs: Job[];
  gpuTotal: number;
  gpuBusy: number;
  gpuAvailable: number;
  gpuUnhealthy: number;
  workerTotal: number;
  workerBusy: number;
}

const EMPTY: DashboardState = {
  metrics: null,
  recentJobs: [],
  gpuTotal: 0,
  gpuBusy: 0,
  gpuAvailable: 0,
  gpuUnhealthy: 0,
  workerTotal: 0,
  workerBusy: 0,
};

const STATUS_VARIANT: Record<string, 'success' | 'warning' | 'danger' | 'info' | 'neutral'> = {
  COMPLETED: 'success',
  RUNNING: 'info',
  STARTING: 'info',
  RESERVED: 'info',
  PAUSED: 'warning',
  RETRYING: 'warning',
  UNRESERVABLE: 'warning',
  FAILED: 'danger',
  CANCELLED: 'neutral',
};

export function Dashboard() {
  const [state, setState] = useState<DashboardState>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { showToast } = useToast();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [metrics, jobs, gpus, workers] = await Promise.all([
        api.metrics(),
        api.listJobs<Job>({ limit: 5 }),
        api.listGpus<{ status: string }>({ limit: 500 }),
        api.listWorkers<{ status: string }>({ limit: 200 }),
      ]);

      const count = (rows: Array<{ status: string }>, value: string) =>
        rows.filter((r) => r.status === value).length;

      setState({
        metrics: metrics as MetricsSnapshot,
        recentJobs: jobs.items,
        gpuTotal: gpus.length,
        gpuBusy: count(gpus, 'BUSY'),
        gpuAvailable: count(gpus, 'AVAILABLE'),
        gpuUnhealthy: count(gpus, 'UNHEALTHY') + count(gpus, 'OFFLINE'),
        workerTotal: workers.items.length,
        workerBusy: count(workers.items, 'BUSY'),
      });
    } catch (err: unknown) {
      setError(extractErrorMessage(err, 'Failed to load dashboard'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const cancel = async (job: Job) => {
    try {
      await api.cancelJob(job.id);
      showToast('success', `Job ${job.id.slice(0, 8)} cancelled`);
      await load();
    } catch (err: unknown) {
      showToast('error', extractErrorMessage(err, 'Failed to cancel job'));
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center my-16">
        <Spinner size="lg" />
      </div>
    );
  }

  const byStatus = state.metrics?.jobs_by_status ?? {};
  const queued = state.metrics?.queued_jobs ?? byStatus.QUEUED ?? 0;
  const running = byStatus.RUNNING ?? 0;
  const completed = byStatus.COMPLETED ?? 0;
  const failed = byStatus.FAILED ?? 0;

  const tiles = [
    { label: 'Queued', value: queued, icon: Clock, to: '/jobs?status=QUEUED', tone: 'text-blue-600' },
    { label: 'Running', value: running, icon: Loader2, to: '/jobs?status=RUNNING', tone: 'text-purple-600' },
    { label: 'Completed', value: completed, icon: CheckCircle, to: '/jobs?status=COMPLETED', tone: 'text-green-600' },
    { label: 'Failed', value: failed, icon: XCircle, to: '/jobs?status=FAILED', tone: 'text-red-600' },
  ];

  return (
    <div className="space-y-6">
      <div className="page-header">
        <div>
          <h1 className="page-title">Dashboard</h1>
          <p className="page-description">
            {state.metrics?.environment ?? 'unknown'} environment · GPU backend{' '}
            {state.metrics?.gpu_backend ?? 'unknown'}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={() => void load()}>
            Refresh
          </Button>
          <Link to="/generate/image">
            <Button>New image job</Button>
          </Link>
        </div>
      </div>

      {error && <div className="alert alert-danger">{error}</div>}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {tiles.map(({ label, value, icon: Icon, to, tone }) => (
          <Link key={label} to={to}>
            <Card className="hover:shadow-md transition-shadow">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm text-muted">{label}</p>
                  <p className="text-3xl font-bold">{value}</p>
                </div>
                <Icon className={`w-8 h-8 ${tone}`} />
              </div>
            </Card>
          </Link>
        ))}
      </div>

      <div className="grid lg:grid-cols-3 gap-6">
        <Card className="lg:col-span-2" header={<h2 className="font-semibold">Recent jobs</h2>}>
          {state.recentJobs.length === 0 ? (
            <EmptyState title="No jobs yet" description="Generate your first image or video to see it here." />
          ) : (
            <div className="table-container">
              <table className="table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Type</th>
                    <th>Status</th>
                    <th>Progress</th>
                    <th>Created</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {state.recentJobs.map((job) => (
                    <tr key={job.id}>
                      <td>
                        <Link to={`/jobs/${job.id}`} className="font-mono text-xs">
                          {job.id.slice(0, 8)}
                        </Link>
                      </td>
                      <td>{job.job_type}</td>
                      <td>
                        <Badge variant={STATUS_VARIANT[job.status] ?? 'neutral'}>{job.status}</Badge>
                      </td>
                      <td>
                        {job.status === 'RUNNING' || job.status === 'STARTING' ? (
                          <div className="progress-bar">
                            <div className="progress-fill" style={{ width: `${job.progress_percent}%` }} />
                          </div>
                        ) : (
                          <span className="text-muted">{job.progress_percent}%</span>
                        )}
                      </td>
                      <td className="text-xs text-muted">{formatDistanceToNow(new Date(job.created_at), { addSuffix: true })}</td>
                      <td>
                        {['QUEUED', 'RESERVED', 'STARTING', 'RUNNING', 'RETRYING'].includes(job.status) && (
                          <Button size="sm" variant="danger" onClick={() => void cancel(job)}>
                            Cancel
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <div className="space-y-6">
          <Card header={<h2 className="font-semibold">GPU cluster</h2>}>
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-2 text-sm">
                  <Cpu className="w-4 h-4" /> Total GPUs
                </span>
                <span className="font-semibold">{state.gpuTotal}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-2 text-sm">
                  <Play className="w-4 h-4" /> Busy
                </span>
                <span className="font-semibold">{state.gpuBusy}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-2 text-sm">
                  <TrendingUp className="w-4 h-4" /> Available
                </span>
                <span className="font-semibold text-green-600">{state.gpuAvailable}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-2 text-sm">
                  <XCircle className="w-4 h-4" /> Unhealthy
                </span>
                <span className="font-semibold text-red-600">{state.gpuUnhealthy}</span>
              </div>
            </div>
          </Card>

          <Card header={<h2 className="font-semibold">Workers</h2>}>
            <div className="flex items-center justify-between">
              <span className="text-sm">Registered workers</span>
              <span className="font-semibold">{state.workerTotal}</span>
            </div>
            <div className="flex items-center justify-between mt-3">
              <span className="text-sm">Busy</span>
              <span className="font-semibold">{state.workerBusy}</span>
            </div>
            {state.workerTotal === 0 && (
              <p className="text-xs text-muted mt-3">
                No workers registered. Start one with <code>uv run app-worker</code>.
              </p>
            )}
          </Card>

          <Card header={<h2 className="font-semibold">Quick actions</h2>}>
            <div className="space-y-2">
              <Link to="/generate/image" className="btn btn-secondary w-full justify-start gap-2">
                <Image size={18} /> Generate image
              </Link>
              <Link to="/generate/video" className="btn btn-secondary w-full justify-start gap-2">
                <Video size={18} /> Generate video
              </Link>
              <Link to="/projects" className="btn btn-secondary w-full justify-start gap-2">
                <FolderKanban size={18} /> Manage projects
              </Link>
              <Link to="/assets" className="btn btn-secondary w-full justify-start gap-2">
                <Box size={18} /> Browse assets
              </Link>
              <Link to="/models" className="btn btn-secondary w-full justify-start gap-2">
                <Database size={18} /> Browse models
              </Link>
            </div>
          </Card>
        </div>
      </div>

      <div className="flex gap-2">
        <Plus size={16} className="text-muted" />
        <span className="text-xs text-muted">
          Lease TTL {state.metrics?.lease_ttl_seconds ?? '—'}s · {state.metrics?.sse_subscribers ?? 0}{' '}
          SSE subscriber(s)
        </span>
      </div>
    </div>
  );
}
