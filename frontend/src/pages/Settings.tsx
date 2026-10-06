import { useEffect, useState, type FormEvent } from 'react';
import { api } from '../api/client';
import { Button, Card, Input, Modal } from '../components/UI';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../hooks/useToast';
import { extractErrorMessage } from '../hooks/useApi';

interface CreatedToken {
  token: string;
  prefix: string;
  expires_at: string;
}

export function Settings() {
  const { user, logout } = useAuth();
  const { showToast } = useToast();

  const [activeTab, setActiveTab] = useState<'profile' | 'tokens'>('profile');
  const [tokenName, setTokenName] = useState('');
  const [scopes, setScopes] = useState('read,write');
  const [ttlDays, setTtlDays] = useState(30);
  const [creating, setCreating] = useState(false);
  const [createdToken, setCreatedToken] = useState<CreatedToken | null>(null);

  useEffect(() => {
    setTokenName('');
  }, []);

  const createToken = async (e: FormEvent) => {
    e.preventDefault();
    if (!tokenName.trim()) {
      showToast('warning', 'Token name is required');
      return;
    }
    setCreating(true);
    try {
      const issued = await api.createToken(
        tokenName.trim(),
        scopes
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean),
        ttlDays,
      );
      setCreatedToken(issued);
      showToast('success', 'API token created');
    } catch (err: unknown) {
      showToast('error', extractErrorMessage(err, 'Failed to create token'));
    } finally {
      setCreating(false);
    }
  };

  const copyToken = async () => {
    if (!createdToken) return;
    try {
      await navigator.clipboard.writeText(createdToken.token);
      showToast('success', 'Token copied to clipboard');
    } catch {
      showToast('error', 'Clipboard unavailable — copy the token manually');
    }
  };

  return (
    <div className="space-y-6 max-w-4xl mx-auto">
      <div className="page-header">
        <div>
          <h1 className="page-title">Settings</h1>
          <p className="page-description">Account and API access</p>
        </div>
      </div>

      <div className="flex gap-2">
        <button
          className={`px-4 py-2 rounded-lg text-sm font-medium ${
            activeTab === 'profile' ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-700'
          }`}
          onClick={() => setActiveTab('profile')}
        >
          Profile
        </button>
        <button
          className={`px-4 py-2 rounded-lg text-sm font-medium ${
            activeTab === 'tokens' ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-700'
          }`}
          onClick={() => setActiveTab('tokens')}
        >
          API tokens
        </button>
      </div>

      {activeTab === 'profile' && (
        <Card header={<h2 className="font-semibold">Profile</h2>}>
          <dl className="space-y-3">
            {[
              ['Username', user?.username],
              ['Display name', user?.display_name],
              ['Email', user?.email ?? '—'],
              ['Roles', user?.roles?.map((r) => r.name).join(', ') || '—'],
              ['Last login', user?.last_login_at ? new Date(user.last_login_at).toLocaleString() : '—'],
            ].map(([label, value]) => (
              <div key={label as string} className="flex justify-between border-b border-gray-100 pb-2">
                <dt className="text-sm text-muted">{label}</dt>
                <dd className="text-sm font-medium">{value}</dd>
              </div>
            ))}
          </dl>
          <p className="text-xs text-muted mt-4">
            Profile fields are read-only: the API exposes no profile update endpoint.
          </p>
          <div className="mt-4">
            <Button variant="danger" onClick={() => void logout()}>
              Sign out
            </Button>
          </div>
        </Card>
      )}

      {activeTab === 'tokens' && (
        <Card header={<h2 className="font-semibold">Create API token</h2>}>
          <form onSubmit={(e) => void createToken(e)} className="space-y-4 max-w-md">
            <Input
              label="Token name"
              required
              placeholder="e.g. CI pipeline"
              value={tokenName}
              onChange={(e) => setTokenName(e.target.value)}
            />
            <Input
              label="Scopes (comma separated)"
              value={scopes}
              onChange={(e) => setScopes(e.target.value)}
              helperText="Granted scopes for this token."
            />
            <Input
              label="TTL (days)"
              type="number"
              min={1}
              max={365}
              value={ttlDays}
              onChange={(e) => setTtlDays(Number(e.target.value))}
            />
            <Button type="submit" loading={creating} disabled={creating}>
              Create token
            </Button>
          </form>

          <p className="text-xs text-muted mt-4">
            Token listing and revocation are not implemented by the API yet, so issued tokens
            cannot be reviewed or removed from this screen.
          </p>
        </Card>
      )}

      <Modal
        isOpen={createdToken !== null}
        onClose={() => setCreatedToken(null)}
        title="Token created"
        footer={
          <>
            <Button variant="secondary" onClick={() => setCreatedToken(null)}>
              Close
            </Button>
            <Button onClick={() => void copyToken()}>Copy to clipboard</Button>
          </>
        }
      >
        {createdToken && (
          <div className="space-y-3">
            <div className="alert alert-warning text-sm">
              This is the only time the token is shown. Store it somewhere safe.
            </div>
            <code className="block p-3 bg-gray-50 rounded font-mono text-sm break-all">
              {createdToken.token}
            </code>
            <p className="text-xs text-muted">
              Prefix {createdToken.prefix} · expires{' '}
              {new Date(createdToken.expires_at).toLocaleString()}
            </p>
          </div>
        )}
      </Modal>
    </div>
  );
}
