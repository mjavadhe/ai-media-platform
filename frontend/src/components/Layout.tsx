import { useState } from 'react';
import { Link, NavLink, Outlet } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import {
  LayoutDashboard,
  FolderKanban,
  Box,
  Image,
  Video,
  Briefcase,
  Cpu,
  Database,
  Settings,
  LogOut,
  Menu,
  X,
  User,
  ChevronDown,
  Bell,
} from 'lucide-react';

const navigation = [
  { name: 'Dashboard', href: '/', icon: LayoutDashboard },
  { name: 'Projects', href: '/projects', icon: FolderKanban },
  { name: 'Assets', href: '/assets', icon: Box },
  { name: 'Image Generator', href: '/generate/image', icon: Image },
  { name: 'Video Generator', href: '/generate/video', icon: Video },
  { name: 'Jobs', href: '/jobs', icon: Briefcase },
  { name: 'GPU Cluster', href: '/gpus', icon: Cpu },
  { name: 'Models', href: '/models', icon: Database },
  { name: 'Workflows', href: '/workflows', icon: Settings },
  { name: 'Logs', href: '/logs', icon: Settings },
  { name: 'Storage', href: '/storage', icon: Settings },
  { name: 'Settings', href: '/settings', icon: Settings },
];

export function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);
  const { user, logout } = useAuth();

  return (
    <aside className={`sidebar ${collapsed ? 'w-16' : ''} transition-all duration-200`}>
      <div className="flex items-center justify-between h-16 px-4 border-b border-gray-200">
        {!collapsed && (
          <Link to="/" className="flex items-center gap-2 text-xl font-bold text-blue-600">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polygon points="23 7 16 12 23 17 23 7"></polygon>
              <rect x="1" y="5" width="15" height="14" rx="2"></rect>
            </svg>
            <span>AI Media</span>
          </Link>
        )}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="p-2 rounded-lg hover:bg-gray-100 text-gray-500"
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <X size={20} /> : <Menu size={20} />}
        </button>
      </div>

      <nav className="flex-1 px-2 py-4 space-y-1 overflow-y-auto">
        {navigation.map((item) => (
          <NavLink
            key={item.name}
            to={item.href}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                isActive
                  ? 'bg-blue-50 text-blue-600'
                  : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
              }`
            }
          >
            <item.icon size={20} aria-hidden="true" />
            {!collapsed && <span>{item.name}</span>}
          </NavLink>
        ))}
      </nav>

      <div className="p-4 border-t border-gray-200">
        {!collapsed ? (
          <div className="flex items-center gap-3">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium truncate">{user?.display_name || user?.username}</p>
              <p className="text-xs text-gray-500 truncate">{user?.email || user?.username}</p>
            </div>
            <button onClick={() => void logout()} className="p-2 rounded-lg hover:bg-gray-100 text-gray-500" title="Logout">
              <LogOut size={18} />
            </button>
          </div>
        ) : (
          <button onClick={() => void logout()} className="p-2 rounded-lg hover:bg-gray-100 text-gray-500" title="Logout">
            <LogOut size={18} />
          </button>
        )}
      </div>
    </aside>
  );
}

export function Header() {
  return (
    <header className="header">
      <div className="flex items-center gap-4">
        <input
          type="text"
          className="input w-64 sm:w-96"
          placeholder="Search projects, assets, jobs..."
        />
      </div>

      <div className="flex items-center gap-4">
        <button className="btn-ghost p-2 relative">
          <Bell size={20} />
          <span className="absolute -top-1 -right-1 w-4 h-4 bg-red-500 text-white text-xs rounded-full flex items-center justify-center">3</span>
        </button>

        <div className="relative">
          <button className="flex items-center gap-2 p-2 rounded-lg hover:bg-gray-100">
            <div className="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center">
              <User size={16} className="text-blue-600" />
            </div>
            <ChevronDown size={16} className="text-gray-500" />
          </button>
        </div>
      </div>
    </header>
  );
}

export function AppLayout() {
  return (
    <div className="app-layout">
      <Sidebar />
      <div className="main-content">
        <Header />
        <main className="page-content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
