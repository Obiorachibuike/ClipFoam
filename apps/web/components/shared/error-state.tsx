'use client';

import { AlertTriangle, RotateCw } from 'lucide-react';

import { Button } from '@/components/ui/button';

export function ErrorState({
  title = 'Something went wrong',
  message,
  onRetry,
}: {
  title?: string;
  message?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="flex flex-col items-start gap-3 rounded-xl border border-red-500/30 bg-red-500/5 p-5" role="alert">
      <div className="flex items-center gap-2 text-sm font-medium text-red-400">
        <AlertTriangle className="h-4 w-4" aria-hidden />
        {title}
      </div>
      {message ? <p className="text-sm text-muted-foreground">{message}</p> : null}
      {onRetry ? (
        <Button size="sm" variant="outline" onClick={onRetry}>
          <RotateCw className="h-3.5 w-3.5" /> Retry
        </Button>
      ) : null}
    </div>
  );
}
