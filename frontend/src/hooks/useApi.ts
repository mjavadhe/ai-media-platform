import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api/client';

/**
 * Error messages are surfaced to the user, so prefer the server's `detail`
 * (FastAPI) over the generic axios message.
 */
export function extractErrorMessage(error: unknown, fallback = 'Something went wrong'): string {
  if (typeof error === 'object' && error !== null) {
    const data = (error as { response?: { data?: { detail?: unknown; message?: string } } })
      .response?.data;
    if (typeof data?.detail === 'string') return data.detail;
    if (Array.isArray(data?.detail) && data.detail.length > 0) {
      const first = data.detail[0] as { msg?: string };
      if (first?.msg) return first.msg;
    }
    if (data?.message) return data.message;
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

export function useApi() {
  const handleError = useCallback(
    (error: unknown, fallback?: string) => extractErrorMessage(error, fallback),
    [],
  );
  return { api, handleError };
}

export interface StreamEvent {
  event_type: string;
  job_id?: string;
  seq?: number;
  [key: string]: unknown;
}

/**
 * Subscribes to the job SSE stream. The caller owns teardown; re-runs on
 * jobId change only.
 */
export function useJobStream(jobId?: string | null) {
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    setEvents([]);
    setError(null);
    if (!jobId) {
      setIsConnected(false);
      return;
    }

    const es = new EventSource(api.streamUrl({ job_id: jobId }));
    sourceRef.current = es;

    es.onopen = () => setIsConnected(true);
    es.onerror = () => setIsConnected(false);
    es.onmessage = (message: MessageEvent<string>) => {
      try {
        const parsed = JSON.parse(message.data) as unknown as StreamEvent;
        // Keep the tail bounded; this is a live view, not an event archive.
        setEvents((prev) => [...prev.slice(-199), parsed]);
      } catch {
        // Malformed frame: ignore rather than tearing down the stream.
      }
    };

    return () => {
      es.close();
      sourceRef.current = null;
    };
  }, [jobId]);

  return { events, isConnected, error, reconnect: () => sourceRef.current?.close() };
}

/** Generic GET-on-mount hook used by the list pages. */
export function useFetch<T>(
  loader: () => Promise<T>,
  deps: unknown[],
): { data: T | null; loading: boolean; error: string | null; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    loader()
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(extractErrorMessage(err, 'Failed to load'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { data, loading, error, reload: () => setNonce((n) => n + 1) };
}
