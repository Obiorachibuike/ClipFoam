'use client';

import Link from 'next/link';
import { useState } from 'react';
import { FolderKanban, Plus, Search } from 'lucide-react';

import { EmptyState } from '@/components/shared/empty-state';
import { ErrorState } from '@/components/shared/error-state';
import { PageHeader } from '@/components/shared/page-header';
import { StatusBadge } from '@/components/shared/status-badge';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { api } from '@/lib/api';
import { aspectLabel, formatDate, timeAgo } from '@/lib/format';
import { useAsync } from '@/lib/hooks';
import type { Page, Project } from '@/lib/types';

export default function ProjectsPage() {
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('all');
  const { data, loading, error, reload } = useAsync<Page<Project>>(
    () =>
      api.get(
        `/projects?limit=60${search ? `&search=${encodeURIComponent(search)}` : ''}${
          status !== 'all' ? `&status=${status}` : ''
        }`,
      ),
    [search, status],
  );

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Library"
        title="Projects"
        description="Each project holds source media, its transcript, candidate clips, rendered exports and scheduled posts."
        actions={
          <Button asChild>
            <Link href="/projects/new">
              <Plus className="h-4 w-4" /> New project
            </Link>
          </Button>
        }
      />

      <div className="flex flex-col gap-2 sm:flex-row">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            className="pl-9"
            placeholder="Search projects…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label="Search projects"
          />
        </div>
        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger className="sm:w-44" aria-label="Filter by status">
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            <SelectItem value="draft">Draft</SelectItem>
            <SelectItem value="active">Active</SelectItem>
            <SelectItem value="archived">Archived</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {error ? (
        <ErrorState message={error} onRetry={reload} />
      ) : loading ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, index) => (
            <Skeleton key={index} className="h-40 w-full" />
          ))}
        </div>
      ) : data?.items.length ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {data.items.map((project) => (
            <Card key={project.id} className="transition-colors hover:border-primary/40">
              <CardContent className="space-y-3 p-5 pt-5">
                <div className="flex items-start justify-between gap-2">
                  <Link href={`/projects/${project.id}`} className="min-w-0">
                    <h3 className="truncate font-medium hover:text-primary">{project.title}</h3>
                    <p className="mt-1 line-clamp-2 min-h-[2.5rem] text-xs leading-relaxed text-muted-foreground">
                      {project.description || 'No description yet.'}
                    </p>
                  </Link>
                  <StatusBadge status={project.status} />
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant="secondary">{aspectLabel(project.default_aspect_ratio)}</Badge>
                  {project.default_platform ? <Badge variant="muted">{project.default_platform}</Badge> : null}
                </div>
                <div className="flex items-center justify-between border-t border-border/70 pt-3 text-xs text-muted-foreground">
                  <span>Updated {timeAgo(project.updated_at)}</span>
                  <span>{formatDate(project.created_at)}</span>
                </div>
                <Button asChild size="sm" variant="secondary" className="w-full">
                  <Link href={`/projects/${project.id}`}>Open project</Link>
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <EmptyState
          icon={FolderKanban}
          title={search ? 'No matching projects' : 'No projects yet'}
          description={
            search
              ? 'Try a different search term or clear the filters.'
              : 'Create a project to upload a long video and start generating clips.'
          }
          action={
            <Button asChild size="sm">
              <Link href="/projects/new">
                <Plus className="h-3.5 w-3.5" /> New project
              </Link>
            </Button>
          }
        />
      )}
    </div>
  );
}
