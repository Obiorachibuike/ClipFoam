'use client';

import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  Captions,
  Film,
  Gauge,
  Layers,
  MoreHorizontal,
  RefreshCw,
  Trash2,
  Video,
} from 'lucide-react';

import { CandidatesPanel } from '@/components/project/candidates-panel';
import { TranscriptPanel } from '@/components/project/transcript-panel';
import { UploadPanel } from '@/components/project/upload-panel';
import { EmptyState } from '@/components/shared/empty-state';
import { ErrorState } from '@/components/shared/error-state';
import { JobProgress } from '@/components/shared/job-progress';
import { PageHeader } from '@/components/shared/page-header';
import { StatusBadge } from '@/components/shared/status-badge';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ConfirmDialog } from '@/components/shared/confirm-dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Skeleton } from '@/components/ui/skeleton';
import { api, errorMessage } from '@/lib/api';
import { aspectLabel, formatBytes, formatDate, formatDuration } from '@/lib/format';
import { useJobStream } from '@/lib/hooks';
import { toast } from '@/lib/store';
import type {
  Clip,
  ClipCandidate,
  Export,
  Job,
  MediaAsset,
  ProjectDetail,
  RuntimeConfig,
  Transcript,
} from '@/lib/types';

export default function ProjectPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const router = useRouter();

  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [media, setMedia] = useState<MediaAsset[]>([]);
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [candidates, setCandidates] = useState<ClipCandidate[]>([]);
  const [clips, setClips] = useState<Clip[]>([]);
  const [exports, setExports] = useState<Export[]>([]);
  const [config, setConfig] = useState<RuntimeConfig | null>(null);
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [seekTo, setSeekTo] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const detail = await api.get<ProjectDetail>(`/projects/${projectId}`);
      setProject(detail);
      const mediaList = await api.get<MediaAsset[]>(`/projects/${projectId}/media`);
      setMedia(mediaList);
      if (mediaList[0]) {
        const existing = await api
          .get<Transcript | null>(`/media/${mediaList[0].id}/transcript`)
          .catch(() => null);
        setTranscript(existing);
      } else {
        setTranscript(null);
      }
      const [candidateList, clipList, exportList] = await Promise.all([
        api.get<ClipCandidate[]>(`/projects/${projectId}/candidates`),
        api.get<Clip[]>(`/projects/${projectId}/clips`),
        api.get<Export[]>(`/projects/${projectId}/exports`),
      ]);
      setCandidates(candidateList);
      setClips(clipList);
      setExports(exportList);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
    void api.get<RuntimeConfig>('/system/config').then(setConfig).catch(() => undefined);
  }, [load]);

  const activeJobs = useMemo(
    () => Object.values(jobs).filter((job) => !['completed', 'failed', 'cancelled'].includes(job.status)),
    [jobs],
  );

  const handleJobUpdate = useCallback((job: Job) => {
    setJobs((current) => ({ ...current, [job.id]: job }));
  }, []);

  const handleJobDone = useCallback(
    (job: Job) => {
      setJobs((current) => ({ ...current, [job.id]: job }));
      if (job.status === 'completed') {
        toast({ title: 'Job completed', description: job.stage_message ?? undefined, variant: 'success' });
        void load();
      } else if (job.status === 'failed') {
        toast({
          title: 'Job failed',
          description: job.error_message ?? 'Check the job details for the provider error.',
          variant: 'error',
        });
        void load();
      }
    },
    [load],
  );

  useJobStream(
    activeJobs.length ? activeJobs[activeJobs.length - 1]?.id ?? null : null,
    handleJobUpdate,
    handleJobDone,
  );

  useEffect(() => {
    if (seekTo === null || !videoRef.current) return;
    videoRef.current.currentTime = seekTo;
    void videoRef.current.play().catch(() => undefined);
    setSeekTo(null);
  }, [seekTo]);

  async function deleteProject() {
    try {
      await api.del(`/projects/${projectId}`);
      toast({ title: 'Project deleted', description: 'Media cleanup was queued.', variant: 'success' });
      router.push('/projects');
    } catch (err) {
      toast({ title: 'Delete failed', description: errorMessage(err), variant: 'error' });
    }
  }

  const primary = media[0] ?? null;
  const transcriptReady = transcript?.status === 'ready';

  if (error && !project) {
    return <ErrorState title="Could not load project" message={error} onRetry={() => void load()} />;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Link href="/projects" className="inline-flex items-center gap-1 hover:text-foreground">
          <ArrowLeft className="h-3.5 w-3.5" /> Projects
        </Link>
        <span>/</span>
        <span className="truncate">{project?.title ?? '…'}</span>
      </div>

      <PageHeader
        eyebrow="Project"
        title={project?.title ?? 'Loading project…'}
        description={project?.description ?? undefined}
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => void load()}>
              <RefreshCw className="h-3.5 w-3.5" /> Refresh
            </Button>
            <Button asChild size="sm" variant="secondary">
              <Link href={`/projects/${projectId}/clips`}>
                <Film className="h-3.5 w-3.5" /> Clips ({clips.length})
              </Link>
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="icon" variant="ghost" aria-label="Project actions">
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <ConfirmDialog
                  trigger={
                    <DropdownMenuItem onSelect={(event) => event.preventDefault()} className="text-red-400">
                      <Trash2 className="h-4 w-4" /> Delete project
                    </DropdownMenuItem>
                  }
                  title="Delete this project?"
                  description="The project, its clips, exports and stored media will be removed. This cannot be undone."
                  confirmLabel="Delete project"
                  destructive
                  onConfirm={deleteProject}
                />
              </DropdownMenuContent>
            </DropdownMenu>
          </>
        }
      />

      {loading && !project ? (
        <div className="space-y-4">
          <Skeleton className="h-56 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      ) : null}

      {project ? (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <StatusBadge status={project.status} />
          <Badge variant="secondary">{aspectLabel(project.default_aspect_ratio)}</Badge>
          {project.default_platform ? <Badge variant="muted">{project.default_platform}</Badge> : null}
          <span>Created {formatDate(project.created_at)}</span>
          <span>·</span>
          <span>{project.clip_count} clips</span>
          <span>·</span>
          <span>{project.export_count} exports</span>
          <span>·</span>
          <span>{project.candidate_count} candidates</span>
        </div>
      ) : null}

      {activeJobs.length ? (
        <div className="space-y-2">
          {activeJobs.map((job) => (
            <JobProgress key={job.id} job={job} />
          ))}
        </div>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[1.4fr_1fr]">
        <div className="space-y-6">
          {primary ? (
            <Card>
              <CardHeader className="flex-row items-start justify-between gap-3">
                <div>
                  <CardTitle className="flex items-center gap-2">
                    <Video className="h-4 w-4 text-primary" aria-hidden /> Source video
                  </CardTitle>
                  <CardDescription className="mt-1 truncate">{primary.original_filename}</CardDescription>
                </div>
                <StatusBadge status={primary.status} />
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="overflow-hidden rounded-lg border border-border bg-black">
                  {primary.stream_url ? (
                    <video
                      ref={videoRef}
                      src={primary.stream_url}
                      poster={primary.thumbnail_url ?? undefined}
                      controls
                      preload="metadata"
                      className="aspect-video w-full"
                    />
                  ) : (
                    <div className="flex aspect-video items-center justify-center text-xs text-muted-foreground">
                      {primary.status === 'ready' ? 'Preview unavailable for this codec' : 'Processing media…'}
                    </div>
                  )}
                </div>
                <dl className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
                  {[
                    ['Duration', formatDuration(primary.duration_seconds)],
                    ['Resolution', primary.width && primary.height ? `${primary.width}×${primary.height}` : '—'],
                    ['Frame rate', primary.fps ? `${primary.fps} fps` : '—'],
                    ['Size', formatBytes(primary.size_bytes)],
                  ].map(([label, value]) => (
                    <div key={label} className="rounded-lg border border-border bg-secondary/40 px-3 py-2">
                      <dt className="mono-label">{label}</dt>
                      <dd className="mt-0.5 text-sm font-medium tabular-nums">{value}</dd>
                    </div>
                  ))}
                </dl>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
                  <span>Video: {primary.video_codec ?? '—'}</span>
                  <span>Audio: {primary.audio_codec ?? (primary.has_audio ? '—' : 'none')}</span>
                  <span>Source: {primary.source_kind.replace('_', ' ')}</span>
                  <span>Type: {primary.mime_type}</span>
                </div>
                {primary.error_message ? (
                  <p className="rounded-md bg-red-500/10 px-3 py-2 text-xs text-red-300">{primary.error_message}</p>
                ) : null}
              </CardContent>
            </Card>
          ) : null}

          <TranscriptPanel
            media={primary}
            transcript={transcript}
            loading={loading}
            onRefresh={() => void load()}
            onJobStarted={(jobId) => {
              if (jobId) setJobs((current) => ({ ...current, [jobId]: { id: jobId } as Job }));
            }}
            onSeek={(seconds) => setSeekTo(seconds)}
          />

          <CandidatesPanel
            projectId={projectId}
            candidates={candidates}
            clips={clips}
            loading={loading}
            transcriptReady={transcriptReady}
            aiConfigured={Boolean(config?.ai.configured)}
            aiLabel={config ? `${config.ai.label}${config.ai.configured ? '' : ' — not configured'}` : 'checking…'}
            onJobStarted={(jobId) => {
              if (jobId) setJobs((current) => ({ ...current, [jobId]: { id: jobId } as Job }));
            }}
            onRefresh={() => void load()}
            onSeek={(seconds) => setSeekTo(seconds)}
          />
        </div>

        <div className="space-y-6">
          <UploadPanel
            projectId={projectId}
            disabled={activeJobs.some((job) => job.type === 'media.ingest' || job.type === 'media.import')}
            onStarted={(jobId) => {
              if (jobId) setJobs((current) => ({ ...current, [jobId]: { id: jobId } as Job }));
              else void load();
            }}
          />

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Layers className="h-4 w-4 text-primary" aria-hidden /> Clips
              </CardTitle>
              <CardDescription>Approved candidates become editable clips.</CardDescription>
            </CardHeader>
            <CardContent>
              {clips.length ? (
                <ul className="divide-y divide-border/70">
                  {clips.map((clip) => (
                    <li key={clip.id} className="flex items-center justify-between gap-3 py-2.5">
                      <div className="min-w-0">
                        <Link href={`/editor/${clip.id}`} className="truncate text-sm font-medium hover:text-primary">
                          {clip.title}
                        </Link>
                        <p className="mt-0.5 text-[11px] text-muted-foreground">
                          {formatDuration(clip.end_seconds - clip.start_seconds)} · {aspectLabel(clip.aspect_ratio)}
                        </p>
                      </div>
                      <StatusBadge status={clip.status} />
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState
                  icon={Film}
                  title="No clips yet"
                  description="Approve a candidate above to create your first clip."
                  className="py-8"
                />
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Gauge className="h-4 w-4 text-primary" aria-hidden /> Exports
              </CardTitle>
              <CardDescription>Rendered files with their exact configuration.</CardDescription>
            </CardHeader>
            <CardContent>
              {exports.length ? (
                <ul className="divide-y divide-border/70">
                  {exports.slice(0, 8).map((item) => (
                    <li key={item.id} className="flex items-center justify-between gap-3 py-2.5">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">
                          {item.kind === 'video' ? 'MP4 export' : item.kind === 'subtitles' ? 'Subtitles' : 'Thumbnail'}
                        </p>
                        <p className="mt-0.5 text-[11px] text-muted-foreground">
                          {item.width && item.height ? `${item.width}×${item.height} · ` : ''}
                          {formatBytes(item.size_bytes)} · {formatDate(item.created_at)}
                        </p>
                      </div>
                      {item.status === 'ready' ? (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={async () => {
                            try {
                              const link = await api.get<{ url: string }>(`/exports/${item.id}/download`);
                              window.open(link.url, '_blank', 'noopener');
                            } catch (err) {
                              toast({ title: 'Download failed', description: errorMessage(err), variant: 'error' });
                            }
                          }}
                        >
                          Download
                        </Button>
                      ) : (
                        <StatusBadge status={item.status} />
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState
                  icon={Captions}
                  title="No exports yet"
                  description="Open a clip in the editor and render it to produce an MP4."
                  className="py-8"
                />
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
