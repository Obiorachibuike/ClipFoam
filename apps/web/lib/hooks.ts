'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { api, errorMessage } from '@/lib/api';
import type { Job } from '@/lib/types';

export function useAsync<T>(loader: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    loaderRef
      .current()
      .then((result) => {
        if (!cancelled) setData(result);
      })
      .catch((err) => {
        if (!cancelled) setError(errorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { data, loading, error, reload, setData };
}

const TERMINAL = ['completed', 'failed', 'cancelled'];

export function useJobStream(jobId: string | null, onUpdate: (job: Job) => void, onDone?: (job: Job) => void) {
  const callback = useRef(onUpdate);
  callback.current = onUpdate;
  const done = useRef(onDone);
  done.current = onDone;

  useEffect(() => {
    if (!jobId) return;
    let source: EventSource | null = null;
    let closed = false;

    const poll = async () => {
      try {
        const job = await api.get<Job>(`/jobs/${jobId}`);
        callback.current(job);
        if (TERMINAL.includes(job.status)) {
          done.current?.(job);
          return true;
        }
      } catch {
        /* transient */
      }
      return false;
    };

    const start = async () => {
      const finished = await poll();
      if (finished || closed) return;
      source = new EventSource(`/api/v1/jobs/${jobId}/events`);
      source.addEventListener('job', (event) => {
        const job = JSON.parse((event as MessageEvent).data) as Job;
        callback.current(job);
        if (TERMINAL.includes(job.status)) {
          source?.close();
          done.current?.(job);
        }
      });
      source.onerror = () => {
        source?.close();
        source = null;
        // Fall back to polling when SSE is unavailable (proxies, restarts).
        setTimeout(() => {
          if (!closed) void start();
        }, 3000);
      };
    };

    void start();
    return () => {
      closed = true;
      source?.close();
    };
  }, [jobId]);
}

export function useInterval(callback: () => void, delay: number | null) {
  const saved = useRef(callback);
  saved.current = callback;
  useEffect(() => {
    if (delay === null) return;
    const id = setInterval(() => saved.current(), delay);
    return () => clearInterval(id);
  }, [delay]);
}

export function useCopy() {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }, []);
  return { copied, copy };
}
