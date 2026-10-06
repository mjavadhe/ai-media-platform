import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { Badge, Button, Card, EmptyState, Input, Modal, Select, Table, Textarea } from '../components/UI';
import { Plus } from 'lucide-react';
import { useToast } from '../hooks/useToast';
import { extractErrorMessage } from '../hooks/useApi';
import { Project } from '../types/api';

/** Matches AssetOut: note the field is `type`, not `asset_type`. */
interface Asset {
  id: string;
  project_id: string | null;
  name: string;
  type: string;
  description: string | null;
  tags: string[];
}

const PAGE_SIZE = 50;

const ASSET_TYPES = [
  'character',
  'location',
  'prop',
  'vehicle',
  'environment',
  'other',
];

export function Assets() {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [offset, setOffset] = useState(0);

  const [showCreateModal, setShowCreateModal] = useState(false);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState({ name: '', type: 'character', description: '', project_id: '' });

  const { showToast } = useToast();

  const fetchAssets = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [assetList, projectList] = await Promise.all([
        api.listAssets<Asset>({ limit: PAGE_SIZE, offset, asset_type: typeFilter || undefined }),
        api.listProjects<Project>({ limit: 100 }),
      ]);
      setAssets(assetList);
      setProjects(projectList);
    } catch (err: unknown) {
      setError(extractErrorMessage(err, 'Failed to load assets'));
    } finally {
      setLoading(false);
    }
  }, [offset, typeFilter]);

  useEffect(() => {
    void fetchAssets();
  }, [fetchAssets]);

  const createAsset = async () => {
    if (!draft.name.trim()) {
      showToast('warning', 'Asset name is required');
      return;
    }
    setCreating(true);
    try {
      await api.createAsset({
        name: draft.name.trim(),
        type: draft.type,
        description: draft.description.trim() || null,
        project_id: draft.project_id || null,
        tags: [],
      });
      showToast('success', 'Asset created');
      setShowCreateModal(false);
      setDraft({ name: '', type: 'character', description: '', project_id: '' });
      await fetchAssets();
    } catch (err: unknown) {
      showToast('error', extractErrorMessage(err, 'Failed to create asset'));
    } finally {
      setCreating(false);
    }
  };

  const filtered = assets.filter((asset) => {
    if (!search.trim()) return true;
    const needle = search.trim().toLowerCase();
    return (
      asset.name.toLowerCase().includes(needle) ||
      asset.type.toLowerCase().includes(needle) ||
      asset.tags.some((t) => t.toLowerCase().includes(needle))
    );
  });

  return (
    <div className="space-y-6">
      <div className="page-header">
        <div>
          <h1 className="page-title">Assets</h1>
          <p className="page-description">Characters, locations, props and other project assets</p>
        </div>
        <Button onClick={() => setShowCreateModal(true)}>
          <Plus size={16} /> New asset
        </Button>
      </div>

      {error && <div className="alert alert-danger">{error}</div>}

      <Card>
        <div className="card-header flex flex-wrap gap-4 items-end">
          <div style={{ width: 260 }}>
            <Input
              label="Search"
              placeholder="Name, type or tag"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div style={{ width: 200 }}>
            <Select
              label="Type"
              options={[
                { value: '', label: 'All types' },
                ...ASSET_TYPES.map((t) => ({ value: t, label: t })),
              ]}
              value={typeFilter}
              onChange={(e) => {
                setTypeFilter(e.target.value);
                setOffset(0);
              }}
            />
          </div>
        </div>

        <div className="card-content">
          <Table<Asset>
            columns={[
              {
                key: 'name',
                header: 'Name',
                render: (asset) => (
                  <div>
                    <Link to={`/assets/${asset.id}`} className="font-medium hover:text-blue-600">
                      {asset.name}
                    </Link>
                    {asset.description && (
                      <p className="text-xs text-muted truncate max-w-md">{asset.description}</p>
                    )}
                  </div>
                ),
              },
              {
                key: 'type',
                header: 'Type',
                render: (asset) => <Badge variant="info">{asset.type}</Badge>,
              },
              {
                key: 'tags',
                header: 'Tags',
                render: (asset) =>
                  asset.tags.length === 0 ? (
                    <span className="text-muted">—</span>
                  ) : (
                    asset.tags.map((tag) => (
                      <Badge key={tag} variant="neutral" className="mr-1">
                        {tag}
                      </Badge>
                    ))
                  ),
              },
              {
                key: 'project',
                header: 'Project',
                render: (asset) => {
                  const project = projects.find((p) => p.id === asset.project_id);
                  return project ? project.name : <span className="text-muted">—</span>;
                },
              },
            ]}
            data={filtered}
            keyExtractor={(asset) => asset.id}
            emptyMessage={
              loading ? 'Loading assets...' : 'No assets match the current filters.'
            }
          />
        </div>

        <div className="card-footer flex items-center justify-between">
          <span className="text-sm text-muted">
            Showing {assets.length === 0 ? 0 : offset + 1}–{offset + assets.length}
          </span>
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
              disabled={assets.length < PAGE_SIZE}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next
            </Button>
          </div>
        </div>
      </Card>

      <Modal
        isOpen={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        title="New asset"
        footer={
          <>
            <Button variant="secondary" onClick={() => setShowCreateModal(false)}>
              Cancel
            </Button>
            <Button loading={creating} disabled={creating} onClick={() => void createAsset()}>
              Create
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Input
            label="Name"
            required
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          />
          <Select
            label="Type"
            options={ASSET_TYPES.map((t) => ({ value: t, label: t }))}
            value={draft.type}
            onChange={(e) => setDraft({ ...draft, type: e.target.value })}
          />
          <Textarea
            label="Description"
            rows={3}
            value={draft.description}
            onChange={(e) => setDraft({ ...draft, description: e.target.value })}
          />
          {projects.length > 0 && (
            <Select
              label="Project"
              placeholder="No project"
              options={projects.map((p) => ({ value: p.id, label: p.name }))}
              value={draft.project_id}
              onChange={(e) => setDraft({ ...draft, project_id: e.target.value })}
            />
          )}
        </div>
      </Modal>

      {!loading && assets.length === 0 && (
        <EmptyState
          title="No assets yet"
          description="Create your first asset to start organising your project."
        />
      )}
    </div>
  );
}
