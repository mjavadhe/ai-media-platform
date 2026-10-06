import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { Button, Card, Table, Input, Modal, Spinner, EmptyState } from '../components/UI';
import { Plus } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { useToast } from '../hooks/useToast';
import { extractErrorMessage } from '../hooks/useApi';
import { Project } from '../types/api';

export function Projects() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [newProject, setNewProject] = useState({ name: '', description: '' });
  const { showToast } = useToast();

  const fetchProjects = useCallback(async () => {
    try {
      setProjects(await api.listProjects<Project>({ limit: 100 }));
    } catch (err: unknown) {
      setError(extractErrorMessage(err, 'Failed to load projects'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void fetchProjects(); }, [fetchProjects]);

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault();
    if (!newProject.name.trim()) return;
    try {
      await api.createProject(newProject);
      showToast('success', 'Project created');
      setShowCreateModal(false);
      setNewProject({ name: '', description: '' });
      void fetchProjects();
    } catch (err: unknown) {
      showToast('error', extractErrorMessage(err, 'Failed to create project'));
    }
  };

  if (loading) return <Spinner size="md" className="mx-auto my-16" />;

  return (
    <div className="space-y-6">
      <div className="page-header">
        <div>
          <h1 className="page-title">Projects</h1>
          <p className="page-description">Manage your media projects</p>
        </div>
        <Button onClick={() => setShowCreateModal(true)}>
          <Plus size={18} /> New Project
        </Button>
      </div>

      {error && <div className="text-red-600 p-4 bg-red-50 rounded-lg">{error}</div>}

      <Card>
        <div className="card-header flex flex-wrap gap-4">
          <div className="flex-1">
            <Input
              placeholder="Search projects..."
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-64"
            />
          </div>
        </div>
        <div className="card-content">
          {projects.length === 0 ? (
            <EmptyState
              icon="📁"
              title="No projects yet"
              description="Create your first project to start organizing your media work"
            />
          ) : (
            <Table<Project>
              columns={[
                { key: 'name', header: 'Name', render: row => (
                  <Link to={`/projects/${row.id}`} className="font-medium hover:text-blue-600">
                    {row.name}
                  </Link>
                )},
                { key: 'description', header: 'Description', render: row => row.description || <span className="text-muted">—</span> },
                { key: 'sequences_count', header: 'Sequences', render: row => row.sequences_count },
                { key: 'shots_count', header: 'Shots', render: row => row.shots_count },
                { key: 'updated_at', header: 'Updated', render: row => formatDistanceToNow(new Date(row.updated_at), { addSuffix: true }) },
              ]}
              data={projects.filter(p => p.name.toLowerCase().includes(search.toLowerCase()) || p.description?.toLowerCase().includes(search.toLowerCase()))}
              keyExtractor={row => row.id}
              emptyMessage="No projects match your search"
            />
          )}
        </div>
      </Card>

      <Modal isOpen={showCreateModal} onClose={() => setShowCreateModal(false)} title="Create Project">
        <form onSubmit={(e) => void handleCreate(e)} className="space-y-4">
          <Input label="Project Name" value={newProject.name} onChange={e => setNewProject({ ...newProject, name: e.target.value })} required placeholder="My Awesome Project" />
          <div className="form-group">
            <label className="label">Description</label>
            <textarea
              className="input"
              value={newProject.description}
              onChange={e => setNewProject({ ...newProject, description: e.target.value })}
              placeholder="Optional description"
              rows={3}
            />
          </div>
          <div className="flex justify-end gap-2 pt-4">
            <button type="button" className="btn btn-secondary" onClick={() => setShowCreateModal(false)}>Cancel</button>
            <button type="submit" className="btn btn-primary">Create Project</button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
