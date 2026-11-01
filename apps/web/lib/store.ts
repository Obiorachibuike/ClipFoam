'use client';

import { create } from 'zustand';

import { api, errorMessage } from '@/lib/api';
import type { Session } from '@/lib/types';

interface SessionState {
  session: Session | null;
  loading: boolean;
  error: string | null;
  load: () => Promise<Session | null>;
  login: (email: string, password: string) => Promise<Session>;
  register: (payload: { email: string; password: string; name: string; organization_name?: string }) => Promise<Session>;
  logout: () => Promise<void>;
  setSession: (session: Session | null) => void;
}

export const useSession = create<SessionState>((set, get) => ({
  session: null,
  loading: true,
  error: null,
  setSession: (session) => set({ session }),
  load: async () => {
    if (get().session) return get().session;
    set({ loading: true, error: null });
    try {
      const session = await api.get<Session>('/auth/session');
      set({ session, loading: false });
      return session;
    } catch (error) {
      set({ session: null, loading: false, error: errorMessage(error) });
      return null;
    }
  },
  login: async (email, password) => {
    const session = await api.post<Session>('/auth/login', { email, password });
    set({ session, loading: false, error: null });
    return session;
  },
  register: async (payload) => {
    const session = await api.post<Session>('/auth/register', payload);
    set({ session, loading: false, error: null });
    return session;
  },
  logout: async () => {
    try {
      await api.post('/auth/logout');
    } finally {
      set({ session: null });
    }
  },
}));

export type ToastVariant = 'default' | 'success' | 'error' | 'warning';

export interface Toast {
  id: string;
  title: string;
  description?: string;
  variant: ToastVariant;
}

interface ToastState {
  toasts: Toast[];
  push: (toast: Omit<Toast, 'id'>) => string;
  dismiss: (id: string) => void;
}

export const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  push: (toast) => {
    const id = Math.random().toString(36).slice(2);
    set((state) => ({ toasts: [...state.toasts, { ...toast, id }].slice(-4) }));
    setTimeout(() => set((state) => ({ toasts: state.toasts.filter((item) => item.id !== id) })), 6000);
    return id;
  },
  dismiss: (id) => set((state) => ({ toasts: state.toasts.filter((item) => item.id !== id) })),
}));

export function toast(options: { title: string; description?: string; variant?: ToastVariant }) {
  return useToastStore.getState().push({
    title: options.title,
    description: options.description,
    variant: options.variant ?? 'default',
  });
}

interface ThemeState {
  theme: 'light' | 'dark';
  setTheme: (theme: 'light' | 'dark') => void;
  toggle: () => void;
}

export const useTheme = create<ThemeState>((set, get) => ({
  theme: 'dark',
  setTheme: (theme) => {
    set({ theme });
    if (typeof document !== 'undefined') {
      document.documentElement.classList.toggle('dark', theme === 'dark');
      window.localStorage.setItem('clipfoam-theme', theme);
    }
  },
  toggle: () => get().setTheme(get().theme === 'dark' ? 'light' : 'dark'),
}));
