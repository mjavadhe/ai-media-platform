import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from '../api/client';
import { Badge, Button, Card, EmptyState, Select, Spinner, Textarea, Input } from '../components/UI';
import { ArrowLeft } from 'lucide-react';
import { useToast } from '../hooks/useToast';
import { extractErrorMessage } from '../hooks/useApi';
import { Job, Model, Project, Workflow } from '../types/api';

type GenerationType = 'image' | 'video';
type JobType = 'IMAGE' | 'VIDEO';

const JOB_TYPE: Record<GenerationType, JobType> = {
  image: 'IMAGE',
  video: 'VIDEO',
};

const PRIORITIES = ['LOW', 'NORMAL', 'HIGH', 'URGENT'] as const;

export function Generate({ type }: { type: GenerationType }) {
  const { id: jobId } = useParams<{ id: string }>();
  const { showToast } = useToast();

  const [models, setModels] = useState<Model[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [createdJob, setCreatedJob] = useState<Job | null>(null);

  const [form, setForm] = useState({
    project_id: '',
    shot_id: '',
    model_key: '',
    workflow_key: '',
    prompt: '',
    negative_prompt: '',
    priority: 'NORMAL',
    max_retries: 3,
  });

  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [modelList, workflowList, projectList] = await Promise.all([
        api.listModels<Model>({ job_type: JOB_TYPE[type], status: 'ACTIVE' }),
        api.listWorkflows<Workflow>(),
        api.listProjects<Project>({ limit: 100 }),
      ]);
      setModels(modelList);
      setWorkflows(workflowList);
      setProjects(projectList);
    } catch (err: unknown) {
      showToast('error', extractErrorMessage(err, 'Failed to load generation options'));
    } finally {
      setLoading(false);
    }
  }, [type, showToast]);

  useEffect(() => {
    void load();
  }, [load]);

  // Workflow/model options are scoped to the job type so the form cannot
  // submit an IMAGE workflow for a VIDEO job.
  const availableWorkflows = useMemo(
    () => workflows.filter((w) => w.model_keys.length === 0 || w.model_keys.includes(form.model_key)),
    [workflows, form.model_key],
  );

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.prompt.trim()) {
      showToast('warning', 'A prompt is required');
      return;
    }
    setSubmitting(true);
    try {
      const job = await api.createJob<Job>({
        job_type: JOB_TYPE[type],
        project_id: form.project_id || null,
        shot_id: form.shot_id || null,
        model_key: form.model_key || null,
        workflow_key: form.workflow_key || null,
        prompt: form.prompt.trim(),
        negative_prompt: form.negative_prompt.trim() || null,
        priority: form.priority,
        max_retries: form.max_retries,
        params: {},
      });
      setCreatedJob(job);
      showToast('success', `Job ${job.id.slice(0, 8)} queued`);
    } catch (err: unknown) {
      showToast('error', extractErrorMessage(err, 'Failed to create job'));
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center my-16">
        <Spinner size="lg" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="page-header">
        <div>
          <h1 className="page-title">Generate {type}</h1>
          <p className="page-description">
            Submit a {type} job to the scheduler queue.
          </p>
        </div>
        <Link to="/jobs">
          <Button variant="secondary">
            <ArrowLeft size={16} /> Back to jobs
          </Button>
        </Link>
      </div>

      {jobId && (
        <Card>
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-muted">Job</p>
              <p className="font-mono text-sm">{jobId}</p>
            </div>
            <Link to={`/jobs/${jobId}`}>
              <Button variant="secondary" size="sm">
                View job
              </Button>
            </Link>
          </div>
        </Card>
      )}

      {createdJob && (
        <div className="alert alert-success flex items-center justify-between">
          <span>
            Job <span className="font-mono">{createdJob.id.slice(0, 8)}</span> created with status{' '}
            <Badge variant="info">{createdJob.status}</Badge>
          </span>
          <Link to={`/jobs/${createdJob.id}`}>
            <Button size="sm" variant="secondary">
              Open
            </Button>
          </Link>
        </div>
      )}

      <div className="grid lg:grid-cols-3 gap-6">
        <form onSubmit={(e) => void submit(e)} className="lg:col-span-2 space-y-4">
          <Card header={<h2 className="font-semibold">Job parameters</h2>}>
            <div className="space-y-4">
              <Textarea
                label="Prompt"
                name="prompt"
                rows={6}
                required
                placeholder="Describe what you want to generate"
                value={form.prompt}
                onChange={(e) => set('prompt', e.target.value)}
              />

              <Textarea
                label="Negative prompt"
                name="negative_prompt"
                rows={3}
                placeholder="What to avoid"
                value={form.negative_prompt}
                onChange={(e) => set('negative_prompt', e.target.value)}
              />

              {models.length === 0 ? (
                <div className="alert alert-warning">
                  No active {type} models are registered. Bootstrap a model registry entry or
                  adjust the model status before submitting.
                </div>
              ) : (
                <Select
                  label="Model"
                  placeholder="Select a model"
                  options={models.map((m) => ({
                    value: m.key,
                    label: `${m.name} (${(m.min_vram_mb / 1024).toFixed(1)} GiB)`,
                  }))}
                  value={form.model_key}
                  onChange={(e) => set('model_key', e.target.value)}
                />
              )}

              {availableWorkflows.length > 0 && (
                <Select
                  label="Workflow"
                  placeholder="Use the model's default workflow"
                  options={availableWorkflows.map((w) => ({ value: w.key, label: w.name }))}
                  value={form.workflow_key}
                  onChange={(e) => set('workflow_key', e.target.value)}
                />
              )}

              {projects.length > 0 && (
                <Select
                  label="Project"
                  placeholder="No project"
                  options={projects.map((p) => ({ value: p.id, label: p.name }))}
                  value={form.project_id}
                  onChange={(e) => set('project_id', e.target.value)}
                />
              )}

              <div className="grid grid-cols-2 gap-4">
                <Select
                  label="Priority"
                  options={PRIORITIES.map((p) => ({ value: p, label: p }))}
                  value={form.priority}
                  onChange={(e) => set('priority', e.target.value)}
                />
                <Input
                  label="Max retries"
                  type="number"
                  min={0}
                  max={20}
                  value={form.max_retries}
                  onChange={(e) => set('max_retries', Number(e.target.value))}
                />
              </div>
            </div>
          </Card>

          <div className="flex gap-2">
            <Button type="submit" loading={submitting} disabled={submitting}>
              Queue {type} job
            </Button>
            <Button
              type="button"
              variant="secondary"
              onClick={() =>
                setForm({
                  project_id: '',
                  shot_id: '',
                  model_key: '',
                  workflow_key: '',
                  prompt: '',
                  negative_prompt: '',
                  priority: 'NORMAL',
                  max_retries: 3,
                })
              }
            >
              Reset
            </Button>
          </div>
        </form>

        <div className="space-y-6">
          <Card header={<h2 className="font-semibold">Job type</h2>}>
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between">
                <dt className="text-muted">Type</dt>
                <dd>
                  <Badge variant="info">{JOB_TYPE[type]}</Badge>
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted">Available models</dt>
                <dd>{models.length}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-muted">Available workflows</dt>
                <dd>{availableWorkflows.length}</dd>
              </div>
            </dl>
          </Card>

          {models.length === 0 && (
            <EmptyState
              title="No models available"
              description="Register a model spec so the scheduler can plan VRAM requirements."
            />
          )}
        </div>
      </div>
    </div>
  );
}
