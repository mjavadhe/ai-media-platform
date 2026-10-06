import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import { Badge, Button, Card, EmptyState, Spinner, Table } from '../components/UI';
import { AlertTriangle, Cpu, Gauge, RefreshCw, Thermometer, Zap } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { extractErrorMessage } from '../hooks/useApi';

/** Matches GpuDeviceOut. */
interface GpuDevice {
  id: string;
  node_id: string;
  uuid: string;
  index: number;
  name: string;
  status: string;
  health: string;
  vram_total_mb: number;
  vram_free_mb: number;
  memory_used_mb: number;
  compute_capability: string | null;
  driver_version: string | null;
  utilization_percent: number;
  temperature_c: number | null;
  power_w: number | null;
  error_message: string | null;
}

/** Matches GpuNodeOut. */
interface GpuNode {
  id: string;
  node_key: string;
  name: string;
  endpoint: string | null;
  status: string;
  metadata: Record<string, unknown> | null;
  last_seen_at: string | null;
}

const STATUS_VARIANT: Record<string, 'success' | 'warning' | 'danger' | 'info' | 'neutral'> = {
  AVAILABLE: 'success',
  RESERVED: 'warning',
  BUSY: 'info',
  DRAINING: 'warning',
  UNHEALTHY: 'danger',
  OFFLINE: 'danger',
  MAINTENANCE: 'neutral',
};

const HEALTH_VARIANT: Record<string, 'success' | 'warning' | 'danger' | 'neutral'> = {
  OK: 'success',
  DEGRADED: 'warning',
  ERROR: 'danger',
  UNKNOWN: 'neutral',
};

const REFRESH_MS = 10000;

const gib = (mb: number) => `${(mb / 1024).toFixed(1)} GiB`;

export function GPUs() {
  const [devices, setDevices] = useState<GpuDevice[]>([]);
  const [nodes, setNodes] = useState<GpuNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [deviceList, nodeList] = await Promise.all([
        api.listGpus<GpuDevice>({ limit: 500 }),
        api.listNodes<GpuNode>(),
      ]);
      setDevices(deviceList);
      setNodes(nodeList);
      setError(null);
    } catch (err: unknown) {
      setError(extractErrorMessage(err, 'Failed to load GPU data'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchData();
    const timer = setInterval(() => void fetchData(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [fetchData]);

  if (loading) {
    return (
      <div className="flex justify-center my-16">
        <Spinner size="lg" />
      </div>
    );
  }

  const available = devices.filter((d) => d.status === 'AVAILABLE').length;
  const busy = devices.filter((d) => d.status === 'BUSY').length;
  const unhealthy = devices.filter((d) => d.status === 'UNHEALTHY' || d.health !== 'OK').length;
  const totalVram = devices.reduce((sum, d) => sum + d.vram_total_mb, 0);
  const freeVram = devices.reduce((sum, d) => sum + d.vram_free_mb, 0);

  const columns = [
    {
      key: 'name',
      header: 'Device',
      render: (d: GpuDevice) => (
        <div>
          <p className="font-medium">{d.name}</p>
          <p className="font-mono text-xs text-muted">
            GPU{d.index} · {d.compute_capability ? `cc ${d.compute_capability}` : 'cc unknown'}
          </p>
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (d: GpuDevice) => <Badge variant={STATUS_VARIANT[d.status] ?? 'neutral'}>{d.status}</Badge>,
    },
    {
      key: 'health',
      header: 'Health',
      render: (d: GpuDevice) => <Badge variant={HEALTH_VARIANT[d.health] ?? 'neutral'}>{d.health}</Badge>,
    },
    {
      key: 'vram',
      header: 'VRAM',
      render: (d: GpuDevice) => (
        <div style={{ minWidth: 140 }}>
          <div className="progress-bar">
            <div
              className="progress-fill"
              style={{ width: `${Math.min(100, (d.memory_used_mb / d.vram_total_mb) * 100)}%` }}
            />
          </div>
          <p className="text-xs text-muted">
            {gib(d.memory_used_mb)} / {gib(d.vram_total_mb)}
          </p>
        </div>
      ),
    },
    {
      key: 'util',
      header: 'Util',
      render: (d: GpuDevice) => (
        <span className="flex items-center gap-1">
          <Gauge size={14} /> {d.utilization_percent}%
        </span>
      ),
    },
    {
      key: 'temp',
      header: 'Temp',
      render: (d: GpuDevice) =>
        d.temperature_c === null ? (
          <span className="text-muted">—</span>
        ) : (
          <span className="flex items-center gap-1">
            <Thermometer size={14} /> {d.temperature_c}°C
          </span>
        ),
    },
    {
      key: 'power',
      header: 'Power',
      render: (d: GpuDevice) =>
        d.power_w === null ? (
          <span className="text-muted">—</span>
        ) : (
          <span className="flex items-center gap-1">
            <Zap size={14} /> {d.power_w}W
          </span>
        ),
    },
    {
      key: 'error',
      header: 'Last error',
      render: (d: GpuDevice) =>
        d.error_message ? (
          <span className="flex items-center gap-1 text-xs text-red-600">
            <AlertTriangle size={14} /> {d.error_message.slice(0, 60)}
          </span>
        ) : (
          <span className="text-muted">—</span>
        ),
    },
  ];

  return (
    <div className="space-y-6">
      <div className="page-header">
        <div>
          <h1 className="page-title">GPUs</h1>
          <p className="page-description">Physical devices and their scheduler state</p>
        </div>
        <Button variant="secondary" onClick={() => void fetchData()}>
          <RefreshCw size={16} /> Refresh
        </Button>
      </div>

      {error && <div className="alert alert-danger">{error}</div>}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <Card>
          <p className="text-sm text-muted">Devices</p>
          <p className="text-3xl font-bold">{devices.length}</p>
        </Card>
        <Card>
          <p className="text-sm text-muted">Available</p>
          <p className="text-3xl font-bold text-green-600">{available}</p>
        </Card>
        <Card>
          <p className="text-sm text-muted">Busy</p>
          <p className="text-3xl font-bold text-blue-600">{busy}</p>
        </Card>
        <Card>
          <p className="text-sm text-muted">Unhealthy</p>
          <p className="text-3xl font-bold text-red-600">{unhealthy}</p>
        </Card>
      </div>

      <Card>
        <Table<GpuDevice>
          columns={columns}
          data={devices}
          keyExtractor={(d) => d.id}
          emptyMessage="No GPU devices registered. Start a worker with a GPU backend to populate this table."
        />
      </Card>

      <Card header={<h2 className="font-semibold">Nodes</h2>}>
        {nodes.length === 0 ? (
          <EmptyState title="No nodes" description="No GPU nodes have registered a heartbeat yet." />
        ) : (
          <div className="table-container">
            <table className="table">
              <thead>
                <tr>
                  <th>Node</th>
                  <th>Key</th>
                  <th>Endpoint</th>
                  <th>Status</th>
                  <th>Last seen</th>
                </tr>
              </thead>
              <tbody>
                {nodes.map((node) => (
                  <tr key={node.id}>
                    <td className="flex items-center gap-2">
                      <Cpu size={14} /> {node.name}
                    </td>
                    <td className="font-mono text-xs">{node.node_key}</td>
                    <td className="font-mono text-xs text-muted">{node.endpoint ?? '—'}</td>
                    <td>
                      <Badge variant={STATUS_VARIANT[node.status] ?? 'neutral'}>{node.status}</Badge>
                    </td>
                    <td className="text-xs text-muted">
                      {node.last_seen_at
                        ? formatDistanceToNow(new Date(node.last_seen_at), { addSuffix: true })
                        : 'never'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <p className="text-xs text-muted">
        Total VRAM {gib(totalVram)} · free {gib(freeVram)} across {devices.length} device(s)
      </p>
    </div>
  );
}
