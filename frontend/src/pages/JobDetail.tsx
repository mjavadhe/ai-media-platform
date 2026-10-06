import { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api/client';
import { Badge, Button, Card, Spinner, Table } from '../components/UI';
import { ArrowLeft, Download } from 'lucide-react';
import { useToast } from '../hooks/useToast';
import { extractErrorMessage, useJobStream } from '../hooks/useApi';
import { GeneratedMedia, Job, JobEvent, MediaVersion } from '../types/api';

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

export function JobDetail() {
  const { id = '' } = useParams<{ id: string }>();
  const { showToast } = useToast();

  const [job, setJob] = useState<Job | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [media, setMedia] = useState<GeneratedMedia[]>([]);
  const [versions, setVersions] = useState<Record<string, MediaVersion[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [jobRow, eventRows, mediaRows] = await Promise.all([
        api.getJob<Job>(id),
        api.getJobEvents<JobEvent>(id, { limit: 200 }),
        api.listGeneratedMedia<GeneratedMedia>({ job_id: id }),
      ]);
      setJob(jobRow);
      setEvents(eventRows);
      setMedia(mediaRows);

      // Versions are a separate call per artifact; only for completed media.
      if (mediaRows.length > 0) {
        const entries = await Promise.all(
          mediaRows.map(async (m) => {
            const list = await api.listMediaVersions<MediaVersion>(m.id).catch(() => []);
            return [m.id, list] as const;
          }),
        );
        setVersions(Object.fromEntries(entries));
      } else {
        setVersions({});
      }
    } catch (err: unknown) {
      setError(extractErrorMessage(err, 'Failed to load job'));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  // Live updates while the job is not in a terminal state.
  const { isConnected } = useJobStream(job && ['COMPLETED', 'CANCELLED'].includes(job.status) ? null : id);
  useEffect(() => {
    if (isConnected) void load();
  }, [isConnected, load]);

  const act = async (action: 'cancel' | 'pause' | 'resume' | 'retry') => {
    setPending(true);
    try {
      if (action === 'cancel') await api.cancelJob(id);
      if (action === 'pause') await api.pauseJob(id);
      if (action === 'resume') await api.resumeJob(id);
      if (action === 'retry') await api.retryJob(id);
      showToast('success', `Job ${action} requested`);
      await load();
    } catch (err: unknown) {
      showToast('error', extractErrorMessage(err, `Failed to ${action} job`));
    } finally {
      setPending(false);
    }
  };

  const download = async (mediaId: string) => {
    try {
      await api.downloadMediaToFile(mediaId, `${mediaId.slice(0, 8)}.bin`);
    } catch (err: unknown) {
      showToast('error', extractErrorMessage(err, 'Failed to download artifact'));
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center my-16">
        <Spinner size="lg" />
      </div>
    );
  }

  if (error || !job) {
    return (
      <div className="space-y-4">
        <Link to="/jobs">
          <Button variant="secondary">
            <ArrowLeft size={16} /> Back to jobs
          </Button>
        </Link>
        <div className="alert alert-danger">{error ?? 'Job not found'}</div>
      </div>
    );
  }

  const terminal = job.status === 'COMPLETED' || job.status === 'CANCELLED';

  return (
    <div className="space-y-6">
      <div className="page-header">
        <div>
          <h1 className="page-title font-mono text-xl">{job.id}</h1>
          <p className="page-description">
            {job.job_type} job · {isConnected ? 'live' : 'not streaming'}
          </p>
        </div>
        <div className="flex gap-2">
          <Link to="/jobs">
            <Button variant="secondary">
              <ArrowLeft size={16} /> Back
            </Button>
          </Link>
          {job.status === 'RUNNING' && (
            <Button variant="secondary" disabled={pending} onClick={() => void act('pause')}>
              Pause
            </Button>
          )}
          {job.status === 'PAUSED' && (
            <Button disabled={pending} onClick={() => void act('resume')}>
              Resume
            </Button>
          )}
          {job.status === 'FAILED' && (
            <Button disabled={pending} onClick={() => void act('retry')}>
              Retry
            </Button>
          )}
          {!terminal && (
            <Button variant="danger" disabled={pending} onClick={() => void act('cancel')}>
              Cancel
            </Button>
          )}
        </div>
      </div>

      <div className="grid lg:grid-cols-3 gap-6">
        <Card className="lg:col-span-1" header={<h2 className="font-semibold">Details</h2>}>
          <dl className="space-y-2 text-sm">
            {[
              ['Status', job.status],
              ['Progress', `${job.progress_percent}%`],
              ['Priority', job.priority],
              ['Retries', `${job.retry_count}/${job.max_retries}`],
              ['Worker', job.worker_id ?? '—'],
              ['GPU', job.gpu_device_id ?? '—'],
              ['Node', job.node_id ?? '—'],
              ['Duration', job.duration_seconds !== null ? `${job.duration_seconds}s` : '—'],
              ['Created', new Date(job.created_at).toLocaleString()],
              ['Started', job.started_at ? new Date(job.started_at).toLocaleString() : '—'],
              ['Finished', job.finished_at ? new Date(job.finished_at).toLocaleString() : '—'],
            ].map(([label, value]) => (
              <div key={label} className="flex justify-between border-b border-gray-100 pb-2">
                <dt className="text-muted">{label}</dt>
                <dd className="font-medium text-right break-all">
                  {label === 'Status' ? (
                    <Badge variant={STATUS_VARIANT[String(value)] ?? 'neutral'}>{String(value)}</Badge>
                  ) : (
                    String(value)
                  )}
                </dd>
              </div>
            ))}
          </dl>
          {job.prompt && (
            <div className="mt-4">
              <p className="text-sm text-muted mb-1">Prompt</p>
              <p className="text-sm">{job.prompt}</p>
            </div>
          )}
          {job.error && (
            <div className="alert alert-danger mt-4 text-sm">
              {JSON.stringify(job.error)}
            </div>
          )}
        </Card>

        <Card header={<h2 className="font-semibold">Artifacts ({media.length})</h2>}>
          {media.length === 0 ? (
            <p className="text-sm text-muted">No artifacts yet.</p>
          ) : (
            <div className="space-y-4">
              {media.map((m) => {
                const version = versions[m.id]?.find((v) => v.version_no === m.current_version_no);
                return (
                  <div key={m.id} className="border border-gray-200 rounded-lg p-3">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="font-medium">
                          {m.media_type} · {m.asset_role}
                        </p>
                        <p className="text-xs text-muted font-mono">{m.id}</p>
                      </div>
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => void download(m.id)}
                        disabled={!version}
                      >
                        <Download size={14} /> Download
                      </Button>
                    </div>
                    {version && (
                      <p className="text-xs text-muted mt-2">
                        v{version.version_no} · {version.mime_type} · {version.size_bytes} bytes
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      </div>

      <Card header={<h2 className="font-semibold">Events ({events.length})</h2>}>
        <Table<JobEvent>
          columns={[
            { key: 'seq', header: 'Seq', render: (e) => e.seq },
            {
              key: 'event_type',
              header: 'Event',
              render: (e) => <Badge variant="info">{e.event_type}</Badge>,
            },
            {
              key: 'ts',
              header: 'At',
              render: (e) => new Date(e.ts).toLocaleTimeString(),
            },
            {
              key: 'payload',
              header: 'Payload',
              render: (e) => (
                <span className="font-mono text-xs truncate block max-w-lg">
                  {e.payload ? JSON.stringify(e.payload) : '—'}
                </span>
              ),
            },
          ]}
          data={[...events].reverse()}
          keyExtractor={(e) => String(e.seq)}
          emptyMessage="No events recorded."
        />
      </Card>
    </div>
  );
}
