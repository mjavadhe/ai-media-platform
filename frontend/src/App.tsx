import React from 'react';
import { Routes, Route, Navigate, Link, useLocation } from 'react-router-dom';
import { AppLayout } from './components/Layout';
import { Login } from './pages/Login';
import { Dashboard } from './pages/Dashboard';
import { Projects } from './pages/Projects';
import { Jobs } from './pages/Jobs';
import { JobDetail } from './pages/JobDetail';
import { GPUs } from './pages/GPUs';
import { Models } from './pages/Models';
import { Generate } from './pages/Generate';
import { Assets } from './pages/Assets';
import { Settings } from './pages/Settings';
import { useAuth } from './context/AuthContext';

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return <div className="flex items-center justify-center h-screen"><div className="spinner-lg" /></div>;
  }

  return isAuthenticated ? (
    <>{children}</>
  ) : (
    <Navigate to="/login" replace state={{ from: location }} />
  );
}

function PublicRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth();

  if (isLoading) {
    return <div className="flex items-center justify-center h-screen"><div className="spinner-lg" /></div>;
  }

  return isAuthenticated ? <Navigate to="/" replace /> : <>{children}</>;
}

function NotFound() {
  return (
    <div className="flex items-center justify-center h-screen">
      <div className="text-center">
        <h1 className="text-6xl font-bold text-gray-200">404</h1>
        <p className="text-gray-500 mt-4">Page not found</p>
        <Link to="/" className="text-blue-600 hover:underline mt-4 inline-block">Go home</Link>
      </div>
    </div>
  );
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<PublicRoute><Login /></PublicRoute>} />
      <Route path="/" element={<ProtectedRoute><AppLayout /></ProtectedRoute>}>
        <Route index element={<Dashboard />} />
        <Route path="projects" element={<Projects />} />
        <Route path="projects/:id" element={<div>Project Detail - Coming Soon</div>} />
        <Route path="jobs" element={<Jobs />} />
        <Route path="jobs/:id" element={<JobDetail />} />
        <Route path="gpus" element={<GPUs />} />
        <Route path="gpus/:id" element={<div>GPU Detail - Coming Soon</div>} />
        <Route path="nodes/:id" element={<div>Node Detail - Coming Soon</div>} />
        <Route path="models" element={<Models />} />
        <Route path="workflows" element={<div>Workflows - Coming Soon</div>} />
        <Route path="generate/image" element={<Generate type="image" />} />
        <Route path="generate/video" element={<Generate type="video" />} />
        <Route path="generate/image/:id" element={<Generate type="image" />} />
        <Route path="generate/video/:id" element={<Generate type="video" />} />
        <Route path="assets" element={<Assets />} />
        <Route path="assets/:id" element={<div>Asset Detail - Coming Soon</div>} />
        <Route path="settings" element={<Settings />} />
      </Route>
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}

export default App;
