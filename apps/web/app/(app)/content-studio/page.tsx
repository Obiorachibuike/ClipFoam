'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Copy, FolderKanban, Pencil, Sparkles, Trash2, Wand2 } from 'lucide-react';

import { EmptyState } from '@/components/shared/empty-state';
import { ErrorState } from '@/components/shared/error-state';
import { PageHeader } from '@/components/shared/page-header';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { api, errorMessage } from '@/lib/api';
import { PLATFORM_LABELS, formatDate } from '@/lib/format';
import { useCopy } from '@/lib/hooks';
import { toast } from '@/lib/store';
import type { Clip, ContentItem, ContentKind, Page, Project, RuntimeConfig } from '@/lib/types';

const KINDS: { id: ContentKind; label: string }[] = [
  { id: 'title', label: 'Titles' },
  { id: 'hook', label: 'Hooks' },
  { id: 'description', label: 'Descriptions' },
  { id: 'hashtags', label: 'Hashtags' },
  { id: 'caption', label: 'Social captions' },
  { id: 'x_post', label: 'X posts' },
];

const TONES = ['punchy', 'professional', 'casual', 'educational', 'bold'] as const;

export default function ContentStudioPage() {
  const [mode, setMode] = useState<'clip' | 'project'>('clip');
  const [projects, setProjects] = useState<Project[]>([]);
  const [clips, setClips] = useState<Clip[]>([]);
  const [projectId, setProjectId] = useState<string>('');
  const [clipId, setClipId] = useState<string>('');
  const [kinds, setKinds] = useState<ContentKind[]>(['title', 'hook', 'description', 'hashtags']);
  const [tone, setTone] = useState<(typeof TONES)[number]>('punchy');
  const [platform, setPlatform] = useState('tiktok');
  const [count, setCount] = useState('3');
  const [items, setItems] = useState<ContentItem[]>([]);
  const [config, setConfig] = useState<RuntimeConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<ContentItem | null>(null);
  const [draft, setDraft] = useState({ title: '', body: '' });
  const { copy } = useCopy();

  const loadProjects = useCallback(async () => {
    try {
      const data = await api.get<Page<Project>>('/projects?limit=100');
      setProjects(data.items);
      if (data.items[0] && !projectId) setProjectId(data.items[0].id);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void loadProjects();
    void api.get<RuntimeConfig>('/system/config').then(setConfig).catch(() => undefined);
  }, [loadProjects]);

  useEffect(() => {
    if (!projectId) return;
    void api
      .get<Clip[]>(`/projects/${projectId}/clips`)
      .then((list) => {
        setClips(list);
        if (list[0]) setClipId(list[0].id);
        else setClipId('');
      })
      .catch(() => setClips([]));
  }, [projectId]);

  const loadItems = useCallback(async () => {
    if (mode === 'clip' && clipId) {
      const list = await api.get<ContentItem[]>(`/clips/${clipId}/content`).catch(() => []);
      setItems(list);
    } else if (mode === 'project' && projectId) {
      const list = await api.get<ContentItem[]>(`/projects/${projectId}/content`).catch(() => []);
      setItems(list);
    }
  }, [mode, clipId, projectId]);

  useEffect(() => {
    void loadItems();
  }, [loadItems]);

  const aiLabel = useMemo(() => {
    if (!config) return 'checking provider…';
    return config.ai.configured
      ? `${config.ai.label} (${config.ai.id})`
      : `${config.ai.label} — deterministic fallback active`;
  }, [config]);

  async function generate() {
    if (!kinds.length) {
      toast({ title: 'Pick at least one content type', variant: 'error' });
      return;
    }
    setGenerating(true);
    try {
      const endpoint = mode === 'clip' ? `/clips/${clipId}/content/generate` : `/projects/${projectId}/content/generate`;
      if (mode === 'clip' && !clipId) throw new Error('Select a clip first');
      if (mode === 'project' && !projectId) throw new Error('Select a project first');
      const generated = await api.post<ContentItem[]>(endpoint, {
        kinds,
        platform,
        tone,
        count: Number(count),
        provider: null,
      });
      setItems((current) => [...generated, ...current]);
      const provider = generated[0]?.provider ?? 'deterministic';
      toast({
        title: `${generated.length} drafts generated`,
        description:
          provider === 'deterministic'
            ? 'Deterministic phrasing from transcript timestamps — no language model was used.'
            : `Generated with ${provider}.`,
        variant: 'success',
      });
    } catch (err) {
      toast({ title: 'Generation failed', description: errorMessage(err), variant: 'error' });
    } finally {
      setGenerating(false);
    }
  }

  async function saveEdit() {
    if (!editing) return;
    try {
      const updated = await api.patch<ContentItem>(`/content/${editing.id}`, {
        title: draft.title || null,
        body: draft.body,
      });
      setItems((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      setEditing(null);
      toast({ title: 'Draft saved', variant: 'success' });
    } catch (err) {
      toast({ title: 'Could not save', description: errorMessage(err), variant: 'error' });
    }
  }

  async function remove(item: ContentItem) {
    try {
      await api.del(`/content/${item.id}`);
      setItems((current) => current.filter((entry) => entry.id !== item.id));
      toast({ title: 'Draft deleted' });
    } catch (err) {
      toast({ title: 'Delete failed', description: errorMessage(err), variant: 'error' });
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Copy"
        title="Content studio"
        description="Turn transcript-backed clips into titles, hooks, hashtags and captions. Everything you generate is stored and editable."
        actions={
          <Badge variant={config?.ai.configured ? 'default' : 'muted'} className="h-7 px-3">
            <Sparkles className="mr-1 h-3 w-3" /> {aiLabel}
          </Badge>
        }
      />

      {error ? <ErrorState message={error} onRetry={() => void loadProjects()} /> : null}

      <div className="grid gap-6 xl:grid-cols-[360px_1fr]">
        <Card className="h-fit">
          <CardHeader>
            <CardTitle>Generate</CardTitle>
            <CardDescription>Drafts are grounded in the clip transcript and its stored timings.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Tabs value={mode} onValueChange={(value) => setMode(value as 'clip' | 'project')}>
              <TabsList className="grid w-full grid-cols-2">
                <TabsTrigger value="clip">Clip</TabsTrigger>
                <TabsTrigger value="project">Whole project</TabsTrigger>
              </TabsList>
            </Tabs>

            <div className="space-y-1.5">
              <Label>Project</Label>
              <Select value={projectId} onValueChange={setProjectId}>
                <SelectTrigger><SelectValue placeholder={loading ? 'Loading…' : 'Select a project'} /></SelectTrigger>
                <SelectContent>
                  {projects.map((project) => (
                    <SelectItem key={project.id} value={project.id}>{project.title}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {mode === 'clip' ? (
              <div className="space-y-1.5">
                <Label>Clip</Label>
                <Select value={clipId} onValueChange={setClipId}>
                  <SelectTrigger><SelectValue placeholder={clips.length ? 'Select a clip' : 'No clips in this project'} /></SelectTrigger>
                  <SelectContent>
                    {clips.map((clip) => (
                      <SelectItem key={clip.id} value={clip.id}>{clip.title}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            ) : null}

            <div className="space-y-2">
              <Label>Content types</Label>
              <div className="flex flex-wrap gap-1.5">
                {KINDS.map((kind) => {
                  const active = kinds.includes(kind.id);
                  return (
                    <button
                      key={kind.id}
                      type="button"
                      onClick={() =>
                        setKinds(active ? kinds.filter((item) => item !== kind.id) : [...kinds, kind.id])
                      }
                      aria-pressed={active}
                      className={`rounded-full border px-2.5 py-1 text-xs transition-colors ${
                        active
                          ? 'border-primary/50 bg-primary/10 text-foreground'
                          : 'border-border text-muted-foreground hover:text-foreground'
                      }`}
                    >
                      {kind.label}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label>Tone</Label>
                <Select value={tone} onValueChange={(value) => setTone(value as (typeof TONES)[number])}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {TONES.map((value) => (
                      <SelectItem key={value} value={value}>{value}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Variants</Label>
                <Input type="number" min={1} max={6} value={count} onChange={(event) => setCount(event.target.value)} />
              </div>
            </div>

            <div className="space-y-1.5">
              <Label>Platform</Label>
              <Select value={platform} onValueChange={setPlatform}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {Object.entries(PLATFORM_LABELS).map(([value, label]) => (
                    <SelectItem key={value} value={value}>{label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <Button className="w-full" onClick={() => void generate()} loading={generating}>
              <Wand2 className="h-4 w-4" /> Generate drafts
            </Button>
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              {config?.ai.configured
                ? 'A language-model provider is configured; requests are sent server-side only and never include your API keys in the browser.'
                : 'No AI provider is configured, so ClipFoam produces deterministic, template-driven copy from the transcript. It is labelled as such everywhere it appears.'}
            </p>
          </CardContent>
        </Card>

        <div className="space-y-4">
          {editing ? (
            <Card>
              <CardHeader>
                <CardTitle>Edit draft</CardTitle>
                <CardDescription>Editing records a new version; the original stays in the database.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="space-y-1.5">
                  <Label htmlFor="edit-title">Title</Label>
                  <Input
                    id="edit-title"
                    value={draft.title}
                    onChange={(event) => setDraft({ ...draft, title: event.target.value })}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="edit-body">Body</Label>
                  <Textarea
                    id="edit-body"
                    className="min-h-[140px]"
                    value={draft.body}
                    onChange={(event) => setDraft({ ...draft, body: event.target.value })}
                  />
                </div>
                <div className="flex gap-2">
                  <Button size="sm" onClick={() => void saveEdit()}>Save changes</Button>
                  <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>
                </div>
              </CardContent>
            </Card>
          ) : null}

          {items.length ? (
            <div className="grid gap-4 sm:grid-cols-2">
              {items.map((item) => (
                <Card key={item.id}>
                  <CardHeader className="gap-2 pb-3">
                    <div className="flex items-center justify-between gap-2">
                      <Badge variant="secondary" className="capitalize">{item.kind}</Badge>
                      <Badge variant={item.provider === 'deterministic' ? 'muted' : 'default'}>
                        {item.provider === 'deterministic' ? 'deterministic' : item.provider}
                      </Badge>
                    </div>
                    {item.title ? <CardTitle className="text-sm">{item.title}</CardTitle> : null}
                    <CardDescription className="text-[11px]">
                      v{item.version} · {formatDate(item.created_at)} {item.is_edited ? '· edited' : ''}
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <p className={`whitespace-pre-wrap text-sm leading-relaxed ${item.kind === 'hashtags' ? 'font-mono text-xs' : ''}`}>
                      {item.body}
                    </p>
                    <div className="flex gap-1.5">
                      <Button size="sm" variant="secondary" onClick={() => {
                        void copy(item.body);
                        toast({ title: `${item.kind} copied`, variant: 'success' });
                      }}>
                        <Copy className="h-3.5 w-3.5" /> Copy
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          setEditing(item);
                          setDraft({ title: item.title ?? '', body: item.body });
                        }}
                        aria-label="Edit draft"
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => void remove(item)} aria-label="Delete draft">
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          ) : (
            <EmptyState
              icon={Sparkles}
              title="No drafts yet"
              description="Pick a clip and generate titles, hooks or hashtags. Nothing is invented without transcript context."
              action={
                <Button asChild size="sm" variant="secondary">
                  <Link href="/projects">
                    <FolderKanban className="h-3.5 w-3.5" /> Open a project
                  </Link>
                </Button>
              }
            />
          )}
        </div>
      </div>
    </div>
  );
}
