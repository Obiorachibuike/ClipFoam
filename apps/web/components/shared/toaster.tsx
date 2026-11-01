'use client';

import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react';

import { useToastStore } from '@/lib/store';
import { cn } from '@/lib/utils';

const ICONS = {
  default: Info,
  success: CheckCircle2,
  error: XCircle,
  warning: AlertTriangle,
} as const;

const STYLES = {
  default: 'border-border',
  success: 'border-emerald-500/40',
  error: 'border-red-500/40',
  warning: 'border-amber-500/40',
} as const;

const ICON_STYLES = {
  default: 'text-muted-foreground',
  success: 'text-emerald-400',
  error: 'text-red-400',
  warning: 'text-amber-400',
} as const;

export function Toaster() {
  const { toasts, dismiss } = useToastStore();
  return (
    <div
      className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-[min(92vw,380px)] flex-col gap-2"
      role="region"
      aria-label="Notifications"
    >
      {toasts.map((item) => {
        const Icon = ICONS[item.variant];
        return (
          <div
            key={item.id}
            className={cn(
              'pointer-events-auto flex items-start gap-3 rounded-xl border bg-card/95 p-3.5 shadow-lg backdrop-blur animate-slide-up',
              STYLES[item.variant],
            )}
            role="status"
          >
            <Icon className={cn('mt-0.5 h-4 w-4 shrink-0', ICON_STYLES[item.variant])} aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">{item.title}</p>
              {item.description ? (
                <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{item.description}</p>
              ) : null}
            </div>
            <button
              onClick={() => dismiss(item.id)}
              className="rounded p-1 text-muted-foreground transition-colors hover:text-foreground"
              aria-label="Dismiss notification"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
