'use client';

import { AlertCircle, CheckCircle2, Loader2, XCircle } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { JOB_STEPS, jobStepIndex } from '@/lib/format';
import type { Job } from '@/lib/types';
import { cn } from '@/lib/utils';

export function JobProgress({ job, compact = false }: { job: Job; compact?: boolean }) {
  const failed = job.status === 'failed';
  const cancelled = job.status === 'cancelled';
  const done = job.status === 'completed';
  const step = jobStepIndex(job.status);

  return (
    <div className={cn('rounded-lg border p-3.5', failed ? 'border-red-500/40 bg-red-500/5' : 'border-border bg-card/60')}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          {failed ? (
            <XCircle className="h-4 w-4 text-red-400" aria-hidden />
          ) : cancelled ? (
            <AlertCircle className="h-4 w-4 text-amber-400" aria-hidden />
          ) : done ? (
            <CheckCircle2 className="h-4 w-4 text-emerald-400" aria-hidden />
          ) : (
            <Loader2 className="h-4 w-4 animate-spin text-primary" aria-hidden />
          )}
          <p className="truncate text-sm font-medium">
            {job.stage_message ?? job.status.replace(/_/g, ' ')}
          </p>
        </div>
        <Badge variant={failed ? 'destructive' : done ? 'success' : 'muted'}>{job.progress}%</Badge>
      </div>
      <Progress
        value={job.progress}
        className={cn('mt-2.5', done && '[&>div]:bg-emerald-500', failed && '[&>div]:bg-red-500')}
        aria-label="Job progress"
      />
      {!compact ? (
        <div className="mt-2.5 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
          {JOB_STEPS.map((item, index) => (
            <span
              key={item.key}
              className={cn(
                index <= step && !failed ? 'text-foreground' : undefined,
                index === step && !done && !failed ? 'font-medium text-primary' : undefined,
              )}
            >
              {item.label}
            </span>
          ))}
        </div>
      ) : null}
      {job.error_message ? (
        <p className="mt-2 rounded-md bg-red-500/10 px-2 py-1.5 text-xs text-red-300">
          {job.error_code ? <span className="font-mono text-[10px] uppercase">{job.error_code}: </span> : null}
          {job.error_message}
        </p>
      ) : null}
      {job.attempts > 1 ? (
        <p className="mt-1.5 text-[11px] text-muted-foreground">
          Attempt {job.attempts} of {job.max_attempts}
        </p>
      ) : null}
    </div>
  );
}
