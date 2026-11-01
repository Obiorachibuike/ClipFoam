'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useState } from 'react';
import { ArrowLeft, Film, LayoutGrid, List, Plus, Scissors, Search } from 'lucide-react';

import { EmptyState } from '@/components/shared/empty-state';
import { ErrorState } from '@/components/shared/error-state';
import { PageHeader } from '@/components/shared/page-header';
import { StatusBadge } from '@/components/shared/status-badge';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { api } from '@/lib/api';
import { aspectLabel, formatClock, formatDate, formatDuration } from '@/lib/format';
import { useAsync } from '@/lib/hooks';
import type { Clip, Export, ProjectDetail } from '@/lib/types';

export default function ProjectClipsPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const [search, setSearch] = useState('');
  const [view, setView] = useState<'grid' | 'table'>('grid');

  const project = useAsync<ProjectDetail>(() => api.get(`/projects/${projectId}`), [projectId]);
  const clips = useAsync<Clip[]>(() => api.get(`/projects/${projectId}/clips`), [projectId]);
  const exports = useAsync<Export[]>(() => api.get(`/projects/${projectId}/exports`), [projectId]);

  const filtered = (clips.data ?? []).filter((clip) =>
    clip.title.toLowerCase().includes(search.toLowerCase()),
  );

  const latestExport = (clipId: string) =>
    (exports.data ?? []).find((item) => item.clip_id === clipId && item.kind === 'video');

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Link href={`/projects/${projectId}`} className="inline-flex items-center gap-1 hover:text-foreground">
          <ArrowLeft className="h-3.5 w-3.5" /> {project.data?.title ?? 'Project'}
        </Link>
        <span>/</span>
        <span>Clips</span>
      </div>

      <PageHeader
        eyebrow="Production"
        title="Clips"
        description="Every approved candidate, its render status and quick access to the editor."
        actions={
          <>
            <Button asChild size="sm" variant="secondary">
              <Link href={`/projects/${projectId}`}>
                <Plus className="h-3.5 w-3.5" /> Detect more clips
              </Link>
            </Button>
            <div className="flex rounded-lg border border-border p-0.5">
              <Button
                size="sm"
                variant={view === 'grid' ? 'secondary' : 'ghost'}
                onClick={() => setView('grid')}
                aria-label="Grid view"
              >
                <LayoutGrid className="h-3.5 w-3.5" />
              </Button>
              <Button
                size="sm"
                variant={view === 'table' ? 'secondary' : 'ghost'}
                onClick={() => setView('table')}
                aria-label="Table view"
              >
                <List className="h-3.5 w-3.5" />
              </Button>
            </div>
          </>
        }
      />

      <div className="relative max-w-sm">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
        <Input
          className="pl-9"
          placeholder="Search clips…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          aria-label="Search clips"
        />
      </div>

      {clips.error ? (
        <ErrorState message={clips.error} onRetry={clips.reload} />
      ) : clips.loading ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, index) => (
            <Skeleton key={index} className="h-44 w-full" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={Scissors}
          title={search ? 'No clips match your search' : 'No clips in this project yet'}
          description="Approve a highlight candidate to create an editable clip."
          action={
            <Button asChild size="sm">
              <Link href={`/projects/${projectId}`}>Go to candidates</Link>
            </Button>
          }
        />
      ) : view === 'grid' ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {filtered.map((clip) => {
            return (
              <Card key={clip.id} className="overflow-hidden transition-colors hover:border-primary/40">
                <div className="relative aspect-[9/12] max-h-44 w-full overflow-hidden bg-secondary/40">
                  <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
                    <Film className="h-6 w-6" aria-hidden />
                    <span className="text-xs">{formatDuration(clip.end_seconds - clip.start_seconds)}</span>
                  </div>
                  <div className="absolute left-2 top-2">
                    <Badge variant="secondary">{clip.aspect_ratio}</Badge>
                  </div>
                </div>
                <div className="space-y-2 p-4">
                  <div className="flex items-start justify-between gap-2">
                    <h3 className="min-w-0 truncate text-sm font-medium">{clip.title}</h3>
                    <StatusBadge status={clip.status} />
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    {formatClock(clip.start_seconds)} → {formatClock(clip.end_seconds)} · rev {clip.current_revision} · {formatDate(clip.updated_at)}
                  </p>
                  <Button asChild size="sm" className="w-full">
                    <Link href={`/editor/${clip.id}`}>Open editor</Link>
                  </Button>
                </div>
              </Card>
            );
          })}
        </div>
      ) : (
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Title</TableHead>
                <TableHead>Window</TableHead>
                <TableHead>Format</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Export</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.map((clip) => (
                <TableRow key={clip.id}>
                  <TableCell className="max-w-[240px] truncate font-medium">{clip.title}</TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">
                    {formatClock(clip.start_seconds)}–{formatClock(clip.end_seconds)}
                  </TableCell>
                  <TableCell className="text-xs">{aspectLabel(clip.aspect_ratio)}</TableCell>
                  <TableCell><StatusBadge status={clip.status} /></TableCell>
                  <TableCell>
                    {latestExport(clip.id) ? (
                      <StatusBadge status={latestExport(clip.id)?.status ?? 'queued'} />
                    ) : (
                      <span className="text-xs text-muted-foreground">none</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button asChild size="sm" variant="ghost">
                      <Link href={`/editor/${clip.id}`}>Edit</Link>
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
      <p className="text-[11px] text-muted-foreground">
        Clip previews appear here once rendered; the editor shows the live source frame with caption timing.
      </p>
    </div>
  );
}
