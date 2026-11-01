'use client';

import Link from 'next/link';
import { Activity, BarChart3, Database, Download, Film, HardDrive, Send, Timer } from 'lucide-react';

import { EmptyState } from '@/components/shared/empty-state';
import { ErrorState } from '@/components/shared/error-state';
import { PageHeader } from '@/components/shared/page-header';
import { StatCard } from '@/components/shared/stat-card';
import { StatusBadge } from '@/components/shared/status-badge';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { api } from '@/lib/api';
import { formatBytes, formatDate, formatDuration } from '@/lib/format';
import { useAsync } from '@/lib/hooks';
import type { AnalyticsOverview, DashboardStats } from '@/lib/types';

type UsageResponse = {
  series: { kind: string; day: string; quantity: number }[];
  totals: Record<string, number>;
  limits: { transcription_minutes: number; render_minutes: number; storage_mb: number };
  storage: { total_bytes: number; quota_bytes: number; media_count: number; export_count: number; used_percent: number };
};

type ExportHistoryRow = {
  id: string;
  kind: string;
  status: string;
  size_bytes: number;
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
  created_at: string;
  clip_id: string | null;
  project_id: string;
  is_video: boolean;
};

const KIND_LABELS: Record<string, string> = {
  transcription_seconds: 'Transcription',
  render_seconds: 'Rendering',
  upload_bytes: 'Uploads',
  ai_tokens: 'AI tokens',
};

const UNITS: Record<string, string> = {
  transcription_seconds: 'min',
  render_seconds: 'min',
  upload_bytes: 'MB',
  ai_tokens: 'tokens',
};

function toDisplay(kind: string, quantity: number) {
  if (kind === 'upload_bytes') return quantity / (1024 * 1024);
  if (kind.endsWith('_seconds')) return quantity / 60;
  return quantity;
}

export default function AnalyticsPage() {
  const stats = useAsync<DashboardStats>(() => api.get('/analytics/dashboard'), []);
  const overview = useAsync<AnalyticsOverview>(() => api.get('/analytics/overview'), []);
  const usage = useAsync<UsageResponse>(() => api.get('/analytics/usage?days=30'), []);
  const history = useAsync<ExportHistoryRow[]>(() => api.get('/analytics/exports?limit=25'), []);

  const series = usage.data?.series ?? [];
  const byDay = new Map<string, { render: number; transcribe: number }>();
  for (const row of series) {
    const entry = byDay.get(row.day) ?? { render: 0, transcribe: 0 };
    if (row.kind === 'render_seconds') entry.render += row.quantity / 60;
    if (row.kind === 'transcription_seconds') entry.transcribe += row.quantity / 60;
    byDay.set(row.day, entry);
  }
  const days = Array.from(byDay.entries()).slice(-14);
  const maxDay = Math.max(1, ...days.map(([, value]) => value.render + value.transcribe));

  const transcriptionUsed = toDisplay('transcription_seconds', usage.data?.totals.transcription_seconds ?? 0);
  const renderUsed = toDisplay('render_seconds', usage.data?.totals.render_seconds ?? 0);
  const transcriptionLimit = usage.data?.limits.transcription_minutes ?? 0;
  const renderLimit = usage.data?.limits.render_minutes ?? 0;
  const storagePercent = usage.data?.storage.used_percent ?? 0;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Insights"
        title="Analytics & usage"
        description="Only measured values: durations come from processed media, storage from real file sizes, and engagement from provider APIs (or nothing at all)."
        actions={
          <Button asChild variant="outline" size="sm">
            <Link href="/social-accounts">
              <Send className="h-3.5 w-3.5" /> Manage connections
            </Link>
          </Button>
        }
      />

      {stats.error ? <ErrorState message={stats.error} onRetry={stats.reload} /> : null}

      {stats.loading || !stats.data ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} className="h-28 w-full" />
          ))}
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard label="Published posts" value={stats.data.published_posts} icon={Send} hint={`${stats.data.scheduled_posts} scheduled`} />
          <StatCard label="Rendered exports" value={stats.data.exports} icon={Film} hint={`${stats.data.render_minutes.toFixed(1)} min of video`} />
          <StatCard
            label="Transcribed"
            value={`${stats.data.transcription_minutes.toFixed(1)} min`}
            icon={Timer}
            hint={`${stats.data.storage.media_count} source file(s)`}
          />
          <StatCard
            label="Failed jobs (30d)"
            value={stats.data.failed_jobs}
            icon={Activity}
            hint={stats.data.failed_jobs ? 'Open the project to inspect job errors' : 'No failures recorded'}
          />
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-[1.4fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <BarChart3 className="h-4 w-4 text-primary" aria-hidden /> Processing volume
            </CardTitle>
            <CardDescription>Minutes of rendering and transcription per day (last 14 active days)</CardDescription>
          </CardHeader>
          <CardContent>
            {usage.loading ? (
              <Skeleton className="h-48 w-full" />
            ) : days.length ? (
              <div className="space-y-3">
                <div className="flex h-48 items-end gap-1.5">
                  {days.map(([day, value]) => {
                    const total = value.render + value.transcribe;
                    const height = Math.max(4, (total / maxDay) * 100);
                    const renderShare = total ? value.render / total : 0;
                    return (
                      <div key={day} className="group flex flex-1 flex-col items-center gap-1">
                        <div
                          className="relative flex w-full flex-col justify-end overflow-hidden rounded-sm bg-secondary"
                          style={{ height: `${height}%` }}
                          title={`${day}: ${value.render.toFixed(1)} min render · ${value.transcribe.toFixed(1)} min transcribe`}
                        >
                          <div className="w-full bg-primary" style={{ height: `${renderShare * 100}%` }} />
                          <div className="w-full bg-primary/40" style={{ height: `${(1 - renderShare) * 100}%` }} />
                        </div>
                        <span className="text-[9px] text-muted-foreground">{day.slice(5)}</span>
                      </div>
                    );
                  })}
                </div>
                <div className="flex gap-4 text-[11px] text-muted-foreground">
                  <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm bg-primary" /> Render minutes</span>
                  <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm bg-primary/40" /> Transcription minutes</span>
                </div>
              </div>
            ) : (
              <EmptyState
                icon={BarChart3}
                title="No processing recorded yet"
                description="Numbers appear after your first transcription or render completes."
                className="py-10"
              />
            )}
          </CardContent>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Database className="h-4 w-4 text-primary" aria-hidden /> Quota usage
              </CardTitle>
              <CardDescription>Enforced server-side before jobs are accepted</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {[
                { label: 'Transcription minutes', used: transcriptionUsed, limit: transcriptionLimit, unit: 'min' },
                { label: 'Render minutes', used: renderUsed, limit: renderLimit, unit: 'min' },
              ].map((row) => (
                <div key={row.label} className="space-y-1.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">{row.label}</span>
                    <span className="font-mono">
                      {row.used.toFixed(1)} / {row.limit} {row.unit}
                    </span>
                  </div>
                  <Progress value={row.limit ? Math.min(100, (row.used / row.limit) * 100) : 0} />
                </div>
              ))}
              <div className="space-y-1.5">
                <div className="flex items-center justify-between text-xs">
                  <span className="flex items-center gap-1.5 text-muted-foreground">
                    <HardDrive className="h-3 w-3" /> Storage
                  </span>
                  <span className="font-mono">
                    {formatBytes(usage.data?.storage.total_bytes ?? 0)} / {formatBytes(usage.data?.storage.quota_bytes ?? 0)}
                  </span>
                </div>
                <Progress value={Math.min(100, storagePercent)} />
                <p className="text-[11px] text-muted-foreground">
                  {usage.data?.storage.media_count ?? 0} media · {usage.data?.storage.export_count ?? 0} exports
                </p>
              </div>
              {usage.error ? <ErrorState message={usage.error} onRetry={usage.reload} /> : null}
              <div className="space-y-1.5 border-t border-border/70 pt-3">
                <p className="mono-label">Recorded totals</p>
                {Object.entries(usage.data?.totals ?? {}).map(([kind, quantity]) => (
                  <div key={kind} className="flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">{KIND_LABELS[kind] ?? kind}</span>
                    <span className="font-mono">
                      {toDisplay(kind, quantity).toFixed(1)} {UNITS[kind] ?? ''}
                    </span>
                  </div>
                ))}
                {!Object.keys(usage.data?.totals ?? {}).length ? (
                  <p className="text-[11px] text-muted-foreground">No usage recorded yet.</p>
                ) : null}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Publishing</CardTitle>
              <CardDescription>Attempts recorded by the worker</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              <div className="grid grid-cols-2 gap-2">
                <div className="rounded-lg border border-border bg-secondary/40 px-3 py-2">
                  <p className="mono-label">Succeeded</p>
                  <p className="text-lg font-semibold tabular-nums text-primary">{overview.data?.publishes_total ?? 0}</p>
                </div>
                <div className="rounded-lg border border-border bg-secondary/40 px-3 py-2">
                  <p className="mono-label">Failed</p>
                  <p className="text-lg font-semibold tabular-nums text-red-400">{overview.data?.publish_failures ?? 0}</p>
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5 pt-1">
                {Object.entries(overview.data?.posts_by_status ?? {}).map(([status, count]) => (
                  <Badge key={status} variant="muted" className="capitalize">
                    {status}: {count}
                  </Badge>
                ))}
                {!Object.keys(overview.data?.posts_by_status ?? {}).length ? (
                  <span className="text-[11px] text-muted-foreground">No posts created yet.</span>
                ) : null}
              </div>
            </CardContent>
          </Card>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Platform metrics</CardTitle>
          <CardDescription>Pulled from provider APIs only — unavailable metrics are reported as unavailable</CardDescription>
        </CardHeader>
        <CardContent>
          {(overview.data?.platforms ?? []).length ? (
            <ul className="space-y-2">
              {overview.data?.platforms.map((platform) => (
                <li key={platform.platform} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border px-3 py-2.5">
                  <div>
                    <p className="text-sm font-medium capitalize">{platform.platform}</p>
                    <p className="text-[11px] text-muted-foreground">{platform.detail}</p>
                  </div>
                  <Badge variant={platform.status === 'available' ? 'default' : platform.status === 'permission_missing' ? 'warning' : 'muted'}>
                    {platform.status.replace('_', ' ')}
                  </Badge>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState
              icon={Send}
              title="No platform metrics"
              description="Connect a platform with read scope to pull real engagement data. ClipFoam never estimates it."
            />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Download className="h-4 w-4 text-primary" aria-hidden /> Export history
          </CardTitle>
          <CardDescription>Most recent renders across the workspace</CardDescription>
        </CardHeader>
        <CardContent>
          {history.loading ? (
            <Skeleton className="h-40 w-full" />
          ) : history.data?.length ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Created</TableHead>
                  <TableHead>Kind</TableHead>
                  <TableHead>Output</TableHead>
                  <TableHead>Duration</TableHead>
                  <TableHead>Size</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Open</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {history.data.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell className="text-xs text-muted-foreground">{formatDate(row.created_at)}</TableCell>
                    <TableCell className="text-xs capitalize">{row.kind}</TableCell>
                    <TableCell className="font-mono text-xs">
                      {row.width && row.height ? `${row.width}×${row.height}` : '—'}
                    </TableCell>
                    <TableCell className="text-xs">{formatDuration(row.duration_seconds)}</TableCell>
                    <TableCell className="text-xs">{formatBytes(row.size_bytes)}</TableCell>
                    <TableCell><StatusBadge status={row.status} /></TableCell>
                    <TableCell className="text-right">
                      {row.clip_id ? (
                        <Button asChild size="sm" variant="ghost">
                          <Link href={`/editor/${row.clip_id}`}>Editor</Link>
                        </Button>
                      ) : (
                        <span className="text-xs text-muted-foreground">—</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <EmptyState icon={Film} title="No exports yet" description="Render a clip to see it here with its exact output settings." />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
