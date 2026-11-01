'use client';

import Link from 'next/link';
import {
  Activity,
  ArrowRight,
  Captions,
  Clock,
  Film,
  FolderKanban,
  HardDrive,
  Plus,
  Sparkles,
  UploadCloud,
} from 'lucide-react';

import { EmptyState } from '@/components/shared/empty-state';
import { ErrorState } from '@/components/shared/error-state';
import { JobProgress } from '@/components/shared/job-progress';
import { PageHeader } from '@/components/shared/page-header';
import { StatCard } from '@/components/shared/stat-card';
import { StatusBadge } from '@/components/shared/status-badge';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { api } from '@/lib/api';
import { formatBytes, timeAgo } from '@/lib/format';
import { useAsync } from '@/lib/hooks';
import type { DashboardStats, Project } from '@/lib/types';

export default function DashboardPage() {
  const stats = useAsync<DashboardStats>(() => api.get('/analytics/dashboard'), []);
  const projects = useAsync<{ items: Project[] }>(() => api.get('/projects?limit=5'), []);

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Overview"
        title="Creator dashboard"
        description="Everything in this workspace — jobs, exports, storage and publishing status — is read live from the database."
        actions={
          <>
            <Button asChild variant="outline">
              <Link href="/projects">
                <FolderKanban className="h-4 w-4" /> All projects
              </Link>
            </Button>
            <Button asChild>
              <Link href="/projects/new">
                <Plus className="h-4 w-4" /> New project
              </Link>
            </Button>
          </>
        }
      />

      {stats.error ? (
        <ErrorState message={stats.error} onRetry={stats.reload} />
      ) : stats.loading || !stats.data ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} className="h-28 w-full" />
          ))}
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard label="Projects" value={stats.data.projects} icon={FolderKanban} hint={`${stats.data.clips} clips created`} />
          <StatCard
            label="Rendered exports"
            value={stats.data.exports}
            icon={Film}
            hint={`${stats.data.render_minutes.toFixed(1)} min rendered`}
          />
          <StatCard
            label="Transcription"
            value={`${stats.data.transcription_minutes.toFixed(1)} min`}
            icon={Captions}
            hint="Local faster-whisper or imported cues"
          />
          <StatCard
            label="Storage used"
            value={formatBytes(stats.data.storage.total_bytes)}
            icon={HardDrive}
            hint={`${stats.data.storage.used_percent}% of ${formatBytes(stats.data.storage.quota_bytes)}`}
          />
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[1.35fr_1fr]">
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <div>
              <CardTitle>Recent pipeline activity</CardTitle>
              <CardDescription>Live job records — retries, failures and progress included.</CardDescription>
            </div>
            <Activity className="h-4 w-4 text-muted-foreground" aria-hidden />
          </CardHeader>
          <CardContent className="space-y-3">
            {stats.loading ? (
              Array.from({ length: 3 }).map((_, index) => <Skeleton key={index} className="h-16 w-full" />)
            ) : stats.data?.recent_jobs.length ? (
              stats.data.recent_jobs.map((job) => (
                <div key={job.id} className="space-y-1.5">
                  <JobProgress job={job} compact />
                  <div className="flex items-center justify-between px-1 text-[11px] text-muted-foreground">
                    <span className="font-mono">{job.type}</span>
                    <span>{timeAgo(job.created_at)}</span>
                  </div>
                </div>
              ))
            ) : (
              <EmptyState
                icon={Activity}
                title="No jobs yet"
                description="Upload a video in a project to start the ingest → transcribe → clip pipeline."
                className="py-10"
              />
            )}
          </CardContent>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader className="flex-row items-center justify-between">
              <div>
                <CardTitle>Publishing</CardTitle>
                <CardDescription>Scheduled and published posts</CardDescription>
              </div>
              <Sparkles className="h-4 w-4 text-muted-foreground" aria-hidden />
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-center justify-between rounded-lg border border-border bg-secondary/40 px-3 py-2.5">
                <span className="text-sm text-muted-foreground">Scheduled</span>
                <span className="text-sm font-semibold tabular-nums">{stats.data?.scheduled_posts ?? 0}</span>
              </div>
              <div className="flex items-center justify-between rounded-lg border border-border bg-secondary/40 px-3 py-2.5">
                <span className="text-sm text-muted-foreground">Published</span>
                <span className="text-sm font-semibold tabular-nums">{stats.data?.published_posts ?? 0}</span>
              </div>
              <div className="flex items-center justify-between rounded-lg border border-border bg-secondary/40 px-3 py-2.5">
                <span className="text-sm text-muted-foreground">Failed jobs (30d)</span>
                <span className="text-sm font-semibold tabular-nums text-red-400">{stats.data?.failed_jobs ?? 0}</span>
              </div>
              <Button asChild variant="outline" className="w-full">
                <Link href="/calendar">
                  <Clock className="h-4 w-4" /> Open calendar
                </Link>
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Quick actions</CardTitle>
              <CardDescription>Jump straight into the pipeline</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-2">
              <Button asChild variant="secondary" className="justify-start">
                <Link href="/projects/new">
                  <UploadCloud className="h-4 w-4" /> Upload a long video
                </Link>
              </Button>
              <Button asChild variant="secondary" className="justify-start">
                <Link href="/content-studio">
                  <Sparkles className="h-4 w-4" /> Generate post copy
                </Link>
              </Button>
              <Button asChild variant="secondary" className="justify-start">
                <Link href="/analytics">
                  <Activity className="h-4 w-4" /> Review usage & storage
                </Link>
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>

      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <div>
            <CardTitle>Latest projects</CardTitle>
            <CardDescription>Pick up where you left off</CardDescription>
          </div>
          <Button asChild variant="ghost" size="sm">
            <Link href="/projects">
              View all <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </Button>
        </CardHeader>
        <CardContent>
          {projects.loading ? (
            <div className="space-y-2">
              {Array.from({ length: 3 }).map((_, index) => (
                <Skeleton key={index} className="h-14 w-full" />
              ))}
            </div>
          ) : projects.data?.items.length ? (
            <ul className="divide-y divide-border/70">
              {projects.data.items.map((project) => (
                <li key={project.id} className="flex items-center justify-between gap-3 py-3">
                  <div className="min-w-0">
                    <Link href={`/projects/${project.id}`} className="truncate text-sm font-medium hover:text-primary">
                      {project.title}
                    </Link>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      Updated {timeAgo(project.updated_at)} · {project.default_platform ?? 'no target platform'}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant="muted">{project.default_aspect_ratio}</Badge>
                    <StatusBadge status={project.status} />
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState
              icon={FolderKanban}
              title="No projects yet"
              description="Create your first project to upload footage and generate clips."
              action={
                <Button asChild size="sm">
                  <Link href="/projects/new">
                    <Plus className="h-3.5 w-3.5" /> New project
                  </Link>
                </Button>
              }
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
