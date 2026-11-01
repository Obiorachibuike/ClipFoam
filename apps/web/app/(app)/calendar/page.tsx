'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Clock,
  ExternalLink,
  Plus,
  Send,
  Trash2,
  XCircle,
} from 'lucide-react';

import { EmptyState } from '@/components/shared/empty-state';
import { ErrorState } from '@/components/shared/error-state';
import { PageHeader } from '@/components/shared/page-header';
import { StatusBadge } from '@/components/shared/status-badge';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { api, errorMessage } from '@/lib/api';
import { PLATFORM_LABELS, formatDate, timeAgo } from '@/lib/format';
import { toast } from '@/lib/store';
import type { Clip, Export, Page, Project, RuntimeConfig, SocialPost } from '@/lib/types';
import { cn } from '@/lib/utils';

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function ymd(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(
    date.getDate(),
  ).padStart(2, '0')}`;
}

function localDateTimeValue(date: Date) {
  const offset = date.getTimezoneOffset();
  return new Date(date.getTime() - offset * 60000).toISOString().slice(0, 16);
}

export default function CalendarPage() {
  const today = useMemo(() => new Date(), []);
  const [month, setMonth] = useState(() => new Date(today.getFullYear(), today.getMonth(), 1));
  const [posts, setPosts] = useState<SocialPost[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [config, setConfig] = useState<RuntimeConfig | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const [clips, setClips] = useState<Clip[]>([]);
  const [exportsList, setExportsList] = useState<Export[]>([]);
  const [form, setForm] = useState({
    platform: 'tiktok',
    title: '',
    body: '',
    hashtags: '',
    project_id: '',
    clip_id: '',
    export_id: '',
    scheduled_at: localDateTimeValue(new Date(Date.now() + 3600_000)),
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
  });
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const monthStart = useMemo(() => new Date(month.getFullYear(), month.getMonth(), 1), [month]);
  const monthEnd = useMemo(() => new Date(month.getFullYear(), month.getMonth() + 1, 0, 23, 59, 59), [month]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        start: monthStart.toISOString(),
        end: monthEnd.toISOString(),
      });
      const [calendarPosts, runtime] = await Promise.all([
        api.get<SocialPost[]>(`/social/calendar?${params.toString()}`),
        api.get<RuntimeConfig>('/system/config').catch(() => null),
      ]);
      setPosts(calendarPosts);
      setConfig(runtime);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [monthStart, monthEnd]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void api.get<Page<Project>>('/projects?limit=100').then((data) => setProjects(data.items)).catch(() => undefined);
    void api.get<Page<SocialPost>>('/social/posts?limit=100').then((data) => {
      // All org posts, used for the "unscheduled drafts" drawer
      setPosts((current) => (current.length ? current : data.items));
    }).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!form.project_id) {
      setClips([]);
      return;
    }
    void api.get<Clip[]>(`/projects/${form.project_id}/clips`).then(setClips).catch(() => setClips([]));
    void api.get<Export[]>(`/projects/${form.project_id}/exports`).then(setExportsList).catch(() => setExportsList([]));
  }, [form.project_id]);

  const days = useMemo(() => {
    const first = new Date(monthStart);
    const startWeekday = (first.getDay() + 6) % 7; // Monday-first grid
    const gridStart = new Date(first);
    gridStart.setDate(first.getDate() - startWeekday);
    return Array.from({ length: 42 }, (_, index) => {
      const date = new Date(gridStart);
      date.setDate(gridStart.getDate() + index);
      return date;
    });
  }, [monthStart]);

  const byDay = useMemo(() => {
    const map = new Map<string, SocialPost[]>();
    for (const post of posts) {
      const stamp = post.scheduled_at ?? post.published_at ?? post.created_at;
      const key = ymd(new Date(stamp));
      map.set(key, [...(map.get(key) ?? []), post]);
    }
    return map;
  }, [posts]);

  const upcoming = useMemo(
    () =>
      posts
        .filter((post) => (post.scheduled_at ?? post.published_at) && new Date(post.scheduled_at ?? post.published_at!) >= today)
        .sort((a, b) => new Date(a.scheduled_at ?? a.published_at!).getTime() - new Date(b.scheduled_at ?? b.published_at!).getTime())
        .slice(0, 6),
    [posts, today],
  );

  async function createPost(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    try {
      const scheduled = new Date(form.scheduled_at);
      if (Number.isNaN(scheduled.getTime())) throw new Error('Pick a valid date and time');
      const created = await api.post<SocialPost>('/social/posts', {
        platform: form.platform,
        title: form.title.trim() || null,
        body: form.body,
        hashtags: form.hashtags
          .split(/[\s,]+/)
          .map((tag) => tag.replace(/^#/, '').trim())
          .filter(Boolean),
        project_id: form.project_id || null,
        clip_id: form.clip_id || null,
        export_id: form.export_id || null,
        scheduled_at: scheduled.toISOString(),
        timezone: form.timezone,
      });
      toast({
        title: 'Post scheduled',
        description: `${PLATFORM_LABELS[created.platform] ?? created.platform} · ${scheduled.toLocaleString()}`,
        variant: 'success',
      });
      setDialogOpen(false);
      setForm({ ...form, title: '', body: '', hashtags: '', clip_id: '', export_id: '' });
      await load();
    } catch (err) {
      toast({ title: 'Could not schedule post', description: errorMessage(err), variant: 'error' });
    } finally {
      setSaving(false);
    }
  }

  async function unschedule(post: SocialPost) {
    setBusyId(post.id);
    try {
      await api.del(`/social/posts/${post.id}/schedule`);
      toast({ title: 'Post moved back to drafts' });
      await load();
    } catch (err) {
      toast({ title: 'Could not unschedule', description: errorMessage(err), variant: 'error' });
    } finally {
      setBusyId(null);
    }
  }

  async function publish(post: SocialPost) {
    setBusyId(post.id);
    try {
      await api.post(`/social/posts/${post.id}/publish`, {});
      toast({ title: 'Publishing queued', description: 'The background worker is uploading via the platform API.' });
      await load();
    } catch (err) {
      toast({ title: 'Publish unavailable', description: errorMessage(err), variant: 'error' });
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Distribution"
        title="Publishing calendar"
        description="Schedules are stored server-side with timezone; publishing runs through durable jobs and records every attempt."
        actions={
          <Button onClick={() => setDialogOpen(true)}>
            <Plus className="h-4 w-4" /> Schedule post
          </Button>
        }
      />

      {config && !config.social_publish_enabled ? (
        <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-4 py-3 text-xs leading-relaxed text-amber-200">
          <XCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <p className="font-medium">Direct publishing is disabled on this deployment</p>
            <p className="mt-0.5 text-amber-200/80">
              CF_SOCIAL_PUBLISH_ENABLED is false, so the publish endpoint returns a clear
              <span className="font-mono"> publishing_disabled </span>
              error instead of pretending to post. Scheduling, drafts and manual export still work — connect a
              platform and set the flag to enable real uploads.
            </p>
          </div>
        </div>
      ) : null}

      {error ? <ErrorState message={error} onRetry={() => void load()} /> : null}

      <div className="grid gap-6 xl:grid-cols-[1.5fr_1fr]">
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <div>
              <CardTitle>
                {month.toLocaleString(undefined, { month: 'long', year: 'numeric' })}
              </CardTitle>
              <CardDescription>Scheduled and published posts by day</CardDescription>
            </div>
            <div className="flex gap-1">
              <Button
                size="icon"
                variant="ghost"
                aria-label="Previous month"
                onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setMonth(new Date(today.getFullYear(), today.getMonth(), 1))}
              >
                Today
              </Button>
              <Button
                size="icon"
                variant="ghost"
                aria-label="Next month"
                onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}
              >
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-7 gap-px overflow-hidden rounded-lg border border-border bg-border text-xs">
              {WEEKDAYS.map((day) => (
                <div key={day} className="bg-secondary/70 px-2 py-1.5 text-center font-medium text-muted-foreground">
                  {day}
                </div>
              ))}
              {days.map((date) => {
                const key = ymd(date);
                const dayPosts = byDay.get(key) ?? [];
                const isCurrentMonth = date.getMonth() === month.getMonth();
                const isToday = ymd(date) === ymd(today);
                return (
                  <div
                    key={key}
                    className={cn(
                      'min-h-[92px] bg-background p-1.5',
                      !isCurrentMonth && 'bg-secondary/20 text-muted-foreground/50',
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <span className={cn('text-[11px] tabular-nums', isToday && 'font-semibold text-primary')}>
                        {date.getDate()}
                      </span>
                      {dayPosts.length > 2 ? (
                        <span className="text-[10px] text-muted-foreground">+{dayPosts.length - 2}</span>
                      ) : null}
                    </div>
                    <div className="mt-1 space-y-1">
                      {dayPosts.slice(0, 2).map((post) => (
                        <div
                          key={post.id}
                          className="truncate rounded bg-primary/10 px-1.5 py-1 text-[10px] text-foreground"
                          title={post.title ?? post.body}
                        >
                          {PLATFORM_LABELS[post.platform] ?? post.platform}
                          {post.scheduled_at ? ` · ${new Date(post.scheduled_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''}
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
            {loading ? <Skeleton className="mt-4 h-24 w-full" /> : null}
          </CardContent>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Clock className="h-4 w-4 text-primary" aria-hidden /> Upcoming
              </CardTitle>
              <CardDescription>Next scheduled posts in this window</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              {upcoming.length ? (
                upcoming.map((post) => (
                  <div key={post.id} className="rounded-lg border border-border bg-secondary/30 p-3">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">
                          {post.title || post.body.slice(0, 60) || 'Untitled post'}
                        </p>
                        <p className="mt-0.5 text-[11px] text-muted-foreground">
                          {PLATFORM_LABELS[post.platform] ?? post.platform} ·{' '}
                          {new Date(post.scheduled_at ?? post.published_at!).toLocaleString()}
                        </p>
                      </div>
                      <StatusBadge status={post.status} />
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => void publish(post)}
                        loading={busyId === post.id}
                        disabled={post.status === 'published'}
                      >
                        <Send className="h-3 w-3" /> Publish now
                      </Button>
                      {post.scheduled_at ? (
                        <Button size="sm" variant="ghost" onClick={() => void unschedule(post)}>
                          <Trash2 className="h-3 w-3" /> Unschedule
                        </Button>
                      ) : null}
                      {post.published_url ? (
                        <Button asChild size="sm" variant="ghost">
                          <a href={post.published_url} target="_blank" rel="noreferrer noopener">
                            <ExternalLink className="h-3 w-3" /> View
                          </a>
                        </Button>
                      ) : null}
                    </div>
                    {post.error_message ? (
                      <p className="mt-2 rounded bg-red-500/10 px-2 py-1 text-[11px] text-red-300">{post.error_message}</p>
                    ) : null}
                  </div>
                ))
              ) : (
                <EmptyState
                  icon={CalendarDays}
                  title="Nothing scheduled"
                  description="Create a post to fill the calendar; drafts without a date stay off the grid."
                  className="py-8"
                />
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Jobs & attempts</CardTitle>
              <CardDescription>Publishing history with failure reasons</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              {posts.filter((post) => post.attempts.length).length ? (
                posts
                  .filter((post) => post.attempts.length)
                  .slice(0, 5)
                  .map((post) => (
                    <div key={post.id} className="rounded-lg border border-border px-3 py-2 text-xs">
                      <p className="truncate font-medium">{post.title || post.body.slice(0, 50)}</p>
                      {post.attempts.map((attempt) => (
                        <p key={attempt.id} className="mt-1 text-[11px] text-muted-foreground">
                          attempt {attempt.attempt} · {attempt.status}
                          {attempt.error_message ? ` · ${attempt.error_message.slice(0, 70)}` : ''}
                          {attempt.started_at ? ` · ${timeAgo(attempt.started_at)}` : ''}
                        </p>
                      ))}
                    </div>
                  ))
              ) : (
                <p className="py-4 text-center text-xs text-muted-foreground">
                  No publish attempts yet. Attempts are recorded with provider error codes when publishing runs.
                </p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Schedule a post</DialogTitle>
            <DialogDescription>
              Attach a rendered export for video platforms. Posts without a date stay as drafts.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={createPost} className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label>Platform</Label>
                <Select value={form.platform} onValueChange={(value) => setForm({ ...form, platform: value })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {Object.entries(PLATFORM_LABELS).map(([value, label]) => (
                      <SelectItem key={value} value={value}>{label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="scheduled-at">Date & time</Label>
                <Input
                  id="scheduled-at"
                  type="datetime-local"
                  required
                  value={form.scheduled_at}
                  onChange={(event) => setForm({ ...form, scheduled_at: event.target.value })}
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label>Project (optional)</Label>
              <Select
                value={form.project_id}
                onValueChange={(value) => setForm({ ...form, project_id: value, clip_id: '', export_id: '' })}
              >
                <SelectTrigger><SelectValue placeholder="No project" /></SelectTrigger>
                <SelectContent>
                  {projects.map((project) => (
                    <SelectItem key={project.id} value={project.id}>{project.title}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label>Clip (optional)</Label>
                <Select
                  value={form.clip_id}
                  onValueChange={(value) => setForm({ ...form, clip_id: value })}
                  disabled={!clips.length}
                >
                  <SelectTrigger><SelectValue placeholder={clips.length ? 'No clip' : 'No clips'} /></SelectTrigger>
                  <SelectContent>
                    {clips.map((clip) => (
                      <SelectItem key={clip.id} value={clip.id}>{clip.title}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Rendered export (optional)</Label>
                <Select
                  value={form.export_id}
                  onValueChange={(value) => setForm({ ...form, export_id: value })}
                  disabled={!exportsList.filter((item) => item.status === 'ready' && item.kind === 'video').length}
                >
                  <SelectTrigger>
                    <SelectValue
                      placeholder={
                        exportsList.filter((item) => item.status === 'ready').length ? 'No file' : 'No rendered files'
                      }
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {exportsList
                      .filter((item) => item.status === 'ready' && item.kind === 'video')
                      .map((item) => (
                        <SelectItem key={item.id} value={item.id}>
                          {item.width}×{item.height} · {formatDate(item.created_at)}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="post-title">Title</Label>
              <Input
                id="post-title"
                value={form.title}
                maxLength={300}
                onChange={(event) => setForm({ ...form, title: event.target.value })}
                placeholder="Optional headline"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="post-body">Caption</Label>
              <Textarea
                id="post-body"
                className="min-h-[110px]"
                value={form.body}
                onChange={(event) => setForm({ ...form, body: event.target.value })}
                placeholder="Write the post copy, or generate it in the Content Studio."
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="post-hashtags">Hashtags</Label>
              <Input
                id="post-hashtags"
                value={form.hashtags}
                onChange={(event) => setForm({ ...form, hashtags: event.target.value })}
                placeholder="shorts podcast growth"
              />
              <p className="text-[11px] text-muted-foreground">Separated by spaces or commas; stored without the # symbol.</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Badge variant="muted">
                Timezone: {form.timezone}
              </Badge>
              {form.scheduled_at ? (
                <Badge variant="muted">
                  Stored as {new Date(form.scheduled_at).toISOString()}
                </Badge>
              ) : null}
            </div>
            <DialogFooter>
              <Button type="submit" loading={saving}>Schedule post</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

    </div>
  );
}
