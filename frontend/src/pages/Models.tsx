import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import { Badge, Button, Card, Input, Spinner, Table } from '../components/UI';
import { RefreshCw } from 'lucide-react';
import { extractErrorMessage } from '../hooks/useApi';
import { Model, Workflow } from '../types/api';

const gib = (mb: number) => (mb >= 1024 ? `${(mb / 1024).toFixed(1)} GiB` : `${mb} MB`);

export function Models() {
  const [models, setModels] = useState<Model[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'models' | 'workflows'>('models');
  const [search, setSearch] = useState('');

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [modelList, workflowList] = await Promise.all([
        api.listModels<Model>(),
        api.listWorkflows<Workflow>(),
      ]);
      setModels(modelList);
      setWorkflows(workflowList);
    } catch (err: unknown) {
      setError(extractErrorMessage(err, 'Failed to load models and workflows'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  if (loading) {
    return (
      <div className="flex justify-center my-16">
        <Spinner size="lg" />
      </div>
    );
  }

  const needle = search.trim().toLowerCase();
  const matches = (key: string, name: string) =>
    !needle || key.toLowerCase().includes(needle) || name.toLowerCase().includes(needle);

  const modelColumns = [
    {
      key: 'key',
      header: 'Key',
      render: (m: Model) => <span className="font-mono text-sm">{m.key}</span>,
    },
    {
      key: 'name',
      header: 'Name',
      render: (m: Model) => (
        <div>
          <p className="font-medium">{m.name}</p>
          {m.notes && <p className="text-xs text-muted truncate max-w-sm">{m.notes}</p>}
        </div>
      ),
    },
    {
      key: 'family',
      header: 'Family',
      render: (m: Model) => <Badge variant="info">{m.family}</Badge>,
    },
    {
      key: 'job_type',
      header: 'Type',
      render: (m: Model) => <Badge variant="neutral">{m.job_type}</Badge>,
    },
    {
      key: 'precision',
      header: 'Precision',
      render: (m: Model) => m.precision ?? <span className="text-muted">—</span>,
    },
    {
      key: 'capabilities',
      header: 'Capabilities',
      render: (m: Model) =>
        m.required_capabilities.length === 0 ? (
          <span className="text-muted">—</span>
        ) : (
          m.required_capabilities.map((c) => (
            <Badge key={c} variant="neutral" className="mr-1">
              {c}
            </Badge>
          ))
        ),
    },
    {
      key: 'min_vram_mb',
      header: 'Min VRAM',
      render: (m: Model) => gib(m.min_vram_mb),
    },
    {
      key: 'est_seconds',
      header: 'Est. time',
      render: (m: Model) => (m.est_seconds > 0 ? `${m.est_seconds}s` : <span className="text-muted">—</span>),
    },
    {
      key: 'source',
      header: 'Source',
      render: (m: Model) => <Badge variant="neutral">{m.source}</Badge>,
    },
  ];

  const workflowColumns = [
    {
      key: 'key',
      header: 'Key',
      render: (w: Workflow) => <span className="font-mono text-sm">{w.key}</span>,
    },
    {
      key: 'name',
      header: 'Name',
      render: (w: Workflow) => (
        <div>
          <p className="font-medium">{w.name}</p>
          {w.version && <p className="text-xs text-muted">v{w.version}</p>}
        </div>
      ),
    },
    {
      key: 'node_count',
      header: 'Nodes',
      render: (w: Workflow) => w.node_count,
    },
    {
      key: 'capabilities',
      header: 'Capabilities',
      render: (w: Workflow) =>
        w.required_capabilities.length === 0 ? (
          <span className="text-muted">—</span>
        ) : (
          w.required_capabilities.map((c) => (
            <Badge key={c} variant="neutral" className="mr-1">
              {c}
            </Badge>
          ))
        ),
    },
    {
      key: 'model_keys',
      header: 'Compatible models',
      render: (w: Workflow) =>
        w.model_keys.length === 0 ? (
          <span className="text-muted">any</span>
        ) : (
          <span className="font-mono text-xs">{w.model_keys.join(', ')}</span>
        ),
    },
    {
      key: 'min_vram_mb',
      header: 'Min VRAM',
      render: (w: Workflow) => gib(w.min_vram_mb),
    },
  ];

  return (
    <div className="space-y-6">
      <div className="page-header">
        <div>
          <h1 className="page-title">Models &amp; Workflows</h1>
          <p className="page-description">
            Registry entries the scheduler uses for capability and VRAM planning
          </p>
        </div>
        <Button variant="secondary" onClick={() => void fetchData()}>
          <RefreshCw size={16} /> Refresh
        </Button>
      </div>

      {error && <div className="alert alert-danger">{error}</div>}

      <div className="flex gap-2">
        <button
          className={`px-4 py-2 rounded-lg text-sm font-medium ${
            activeTab === 'models' ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-700'
          }`}
          onClick={() => setActiveTab('models')}
        >
          Models ({models.length})
        </button>
        <button
          className={`px-4 py-2 rounded-lg text-sm font-medium ${
            activeTab === 'workflows' ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-700'
          }`}
          onClick={() => setActiveTab('workflows')}
        >
          Workflows ({workflows.length})
        </button>
      </div>

      <div style={{ maxWidth: 320 }}>
        <Input
          label="Search"
          placeholder="Filter by key or name"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <Card>
        {activeTab === 'models' ? (
          <Table<Model>
            columns={modelColumns}
            data={models.filter((m) => matches(m.key, m.name))}
            keyExtractor={(m) => m.key}
            emptyMessage="No models registered."
          />
        ) : (
          <Table<Workflow>
            columns={workflowColumns}
            data={workflows.filter((w) => matches(w.key, w.name))}
            keyExtractor={(w) => w.key}
            emptyMessage="No workflows registered."
          />
        )}
      </Card>
    </div>
  );
}
