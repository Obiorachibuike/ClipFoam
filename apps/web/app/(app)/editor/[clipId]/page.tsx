'use client';

import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  ArrowLeft,
  Captions,
  Check,
  Download,
  Film,
  Gauge,
  Layers,
  Loader2,
  Pause,
  Play,
  RotateCcw,
  Save,
  Scissors,
  Sparkles,
  Volume2,
  Wand2,
} from 'lucide-react';

import { CaptionOverlay } from '@/components/editor/caption-overlay';
import { JobProgress } from '@/components/shared/job-progress';
import { StatusBadge } from '@/components/shared/status-badge';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Slider } from '@/components/ui/slider';
import { Switch } from '@/components/ui/switch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { api, errorMessage } from '@/lib/api';
import { aspectLabel, formatBytes, formatClock, formatDuration, PLATFORM_LABELS } from '@/lib/format';
import { useJobStream } from '@/lib/hooks';
import { toast } from '@/lib/store';
import type {
  AspectRatio,
  CaptionPreview,
  CaptionStyle,
  CaptionTrack,
  Clip,
  ClipSettings,
  Export,
  Job,
  MediaAsset,
  Project,
} from '@/lib/types';
import { cn } from '@/lib/utils';

const PRESET_LABELS: Record<string, string> = {
  karaoke: 'Karaoke',
  bold_creator: 'Bold creator',
  minimal: 'Minimal',
  boxed: 'Boxed',
  podcast: 'Podcast',
  neon: 'Neon',
  classic: 'Classic',
  editorial: 'Editorial',
};

const DEFAULT_SETTINGS: ClipSettings = {
  resolution: '1080p',
  quality: 'standard',
  remove_silence: false,
  silence_min_seconds: 0.6,
  remove_filler_words: false,
  normalize_audio: true,
  background_music: 'none',
  background_music_gain_db: -18,
  reframe_mode: 'center',
  face_tracking: false,
  intro_text: null,
  outro_text: null,
  overlay_text: null,
  overlay_position: 'top',
  logo_enabled: false,
  captions_enabled: true,
  safe_area_margin: 8,
  fps: 30,
};

function CaptionStyleEditor({
  style,
  onChange,
  presets,
  onPreset,
}: {
  style: CaptionStyle;
  onChange: (patch: Partial<CaptionStyle>) => void;
  presets: { id: string; defaults: Partial<CaptionStyle> }[];
  onPreset: (id: string) => void;
}) {
  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <Label>Preset</Label>
        <div className="grid grid-cols-2 gap-2">
          {presets.map((preset) => (
            <button
              key={preset.id}
              type="button"
              onClick={() => onPreset(preset.id)}
              aria-pressed={style.preset === preset.id}
              className={cn(
                'rounded-lg border px-3 py-2 text-left text-xs transition-colors',
                style.preset === preset.id
                  ? 'border-primary/60 bg-primary/10 text-foreground'
                  : 'border-border bg-secondary/30 text-muted-foreground hover:border-border hover:text-foreground',
              )}
            >
              <span className="block text-sm font-medium">{PRESET_LABELS[preset.id] ?? preset.id}</span>
              <span className="mt-0.5 block truncate text-[10px] text-muted-foreground">
                {preset.defaults.words_per_caption ?? 3} words · {preset.defaults.animation ?? 'none'}
              </span>
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <Label htmlFor="font-size">Font size</Label>
          <span className="font-mono text-xs text-muted-foreground">{style.font_size}px @1080p</span>
        </div>
        <Slider
          id="font-size"
          min={28}
          max={110}
          step={1}
          value={[style.font_size]}
          onValueChange={([value]) => onChange({ font_size: value })}
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="text-color">Text</Label>
          <Input
            id="text-color"
            type="color"
            value={style.text_color}
            onChange={(event) => onChange({ text_color: event.target.value.toUpperCase() })}
            className="h-9 p-1"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="highlight-color">Highlight</Label>
          <Input
            id="highlight-color"
            type="color"
            value={style.highlight_color}
            onChange={(event) => onChange({ highlight_color: event.target.value.toUpperCase() })}
            className="h-9 p-1"
          />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label>Position</Label>
          <Select value={style.position} onValueChange={(value) => onChange({ position: value as CaptionStyle['position'] })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="top">Top</SelectItem>
              <SelectItem value="center">Center</SelectItem>
              <SelectItem value="bottom">Bottom</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label>Animation</Label>
          <Select value={style.animation} onValueChange={(value) => onChange({ animation: value as CaptionStyle['animation'] })}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              {['karaoke', 'pop', 'fade', 'slide', 'none'].map((value) => (
                <SelectItem key={value} value={value}>{value}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <Label htmlFor="words-per-caption">Words / caption</Label>
            <span className="font-mono text-xs text-muted-foreground">{style.words_per_caption}</span>
          </div>
          <Slider
            id="words-per-caption"
            min={1}
            max={12}
            step={1}
            value={[style.words_per_caption]}
            onValueChange={([value]) => onChange({ words_per_caption: value })}
          />
        </div>
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <Label htmlFor="max-chars">Chars / line</Label>
            <span className="font-mono text-xs text-muted-foreground">{style.max_chars_per_line}</span>
          </div>
          <Slider
            id="max-chars"
            min={8}
            max={48}
            step={1}
            value={[style.max_chars_per_line]}
            onValueChange={([value]) => onChange({ max_chars_per_line: value })}
          />
        </div>
      </div>

      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <Label htmlFor="bg-opacity">Background box</Label>
          <span className="font-mono text-xs text-muted-foreground">{Math.round(style.background_opacity * 100)}%</span>
        </div>
        <Slider
          id="bg-opacity"
          min={0}
          max={100}
          step={1}
          value={[Math.round(style.background_opacity * 100)]}
          onValueChange={([value]) => onChange({ background_opacity: value / 100 })}
        />
      </div>

      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <Label htmlFor="outline">Outline</Label>
          <span className="font-mono text-xs text-muted-foreground">{style.outline_width.toFixed(1)}px</span>
        </div>
        <Slider
          id="outline"
          min={0}
          max={10}
          step={0.5}
          value={[style.outline_width]}
          onValueChange={([value]) => onChange({ outline_width: value })}
        />
      </div>

      <div className="space-y-1.5">
        <div className="flex items-center justify-between">
          <Label htmlFor="safe-area">Safe-area margin</Label>
          <span className="font-mono text-xs text-muted-foreground">{style.safe_area_margin}%</span>
        </div>
        <Slider
          id="safe-area"
          min={0}
          max={20}
          step={1}
          value={[style.safe_area_margin]}
          onValueChange={([value]) => onChange({ safe_area_margin: value })}
        />
      </div>

      <label className="flex items-center justify-between rounded-lg border border-border bg-secondary/30 px-3 py-2.5 text-sm">
        <span>Uppercase</span>
        <Switch checked={style.uppercase} onCheckedChange={(checked) => onChange({ uppercase: checked })} />
      </label>
    </div>
  );
}

export default function EditorPage() {
  const params = useParams<{ clipId: string }>();
  const clipId = params.clipId;
  const router = useRouter();
  const videoRef = useRef<HTMLVideoElement | null>(null);

  const [clip, setClip] = useState<Clip | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [media, setMedia] = useState<MediaAsset | null>(null);
  const [track, setTrack] = useState<CaptionTrack | null>(null);
  const [preview, setPreview] = useState<CaptionPreview | null>(null);
  const [style, setStyle] = useState<CaptionStyle | null>(null);
  const [settings, setSettings] = useState<ClipSettings>(DEFAULT_SETTINGS);
  const [presets, setPresets] = useState<{ id: string; defaults: Partial<CaptionStyle> }[]>([]);
  const [exportsList, setExportsList] = useState<Export[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [rendering, setRendering] = useState(false);

  const [title, setTitle] = useState('');
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(0);
  const [aspect, setAspect] = useState<AspectRatio>('9:16');

  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);

  const load = useCallback(async () => {
    try {
      const loadedClip = await api.get<Clip>(`/clips/${clipId}`);
      setClip(loadedClip);
      setTitle(loadedClip.title);
      setStart(loadedClip.start_seconds);
      setEnd(loadedClip.end_seconds);
      setAspect(loadedClip.aspect_ratio);
      setSettings({ ...DEFAULT_SETTINGS, ...(loadedClip.settings as Partial<ClipSettings>) });
      const [loadedMedia, loadedTrack, loadedPreview, loadedExports, loadedPresets, loadedProject] =
        await Promise.all([
          api.get<MediaAsset>(`/media/${loadedClip.media_asset_id}`),
          api.get<CaptionTrack>(`/clips/${clipId}/captions`),
          api.get<CaptionPreview>(`/clips/${clipId}/captions/preview`),
          api.get<Export[]>(`/clips/${clipId}/exports`),
          api.get<{ id: string; defaults: Partial<CaptionStyle> }[]>('/brand-kits/presets').catch(() => []),
          api.get<Project>(`/projects/${loadedClip.project_id}`).catch(() => null),
        ]);
      setMedia(loadedMedia);
      setTrack(loadedTrack);
      setPreview(loadedPreview);
      setStyle(loadedPreview.style);
      setExportsList(loadedExports);
      setPresets(loadedPresets);
      setProject(loadedProject);
      setDirty(false);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [clipId]);

  useEffect(() => {
    void load();
  }, [load]);

  const refreshPreview = useCallback(async () => {
    try {
      const fresh = await api.get<CaptionPreview>(`/clips/${clipId}/captions/preview`);
      setPreview(fresh);
    } catch {
      /* preview refresh is best-effort; the render uses the server-side model */
    }
  }, [clipId]);

  const handleJobUpdate = useCallback((next: Job) => setJob(next), []);
  const handleJobDone = useCallback(
    (next: Job) => {
      setJob(next);
      setRendering(false);
      if (next.status === 'completed') {
        toast({ title: 'Render complete', description: 'Your MP4 is ready to download.', variant: 'success' });
        void api.get<Export[]>(`/clips/${clipId}/exports`).then(setExportsList).catch(() => undefined);
        void load();
      } else if (next.status === 'failed') {
        toast({
          title: 'Render failed',
          description: next.error_message ?? 'Check server logs for the FFmpeg error.',
          variant: 'error',
        });
      } else if (next.status === 'cancelled') {
        toast({ title: 'Render cancelled' });
      }
    },
    [clipId, load],
  );

  useJobStream(job && !['completed', 'failed', 'cancelled'].includes(job.status) ? job.id : null, handleJobUpdate, handleJobDone);

  useEffect(() => {
    if (!clip || !videoRef.current) return;
    const element = videoRef.current;
    if (Math.abs(element.currentTime - clip.start_seconds) > 0.35) {
      element.currentTime = clip.start_seconds;
      setTime(clip.start_seconds);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clip]);

  function patchStyle(patch: Partial<CaptionStyle>) {
    if (!style) return;
    setStyle({ ...style, ...patch });
    setDirty(true);
  }

  function applyPreset(presetId: string) {
    const preset = presets.find((item) => item.id === presetId);
    if (!preset || !style) return;
    setStyle({ ...style, ...preset.defaults, preset: presetId as CaptionStyle['preset'] });
    setDirty(true);
  }

  async function saveAll() {
    if (!clip) return;
    setSaving(true);
    try {
      const updated = await api.patch<Clip>(`/clips/${clipId}`, {
        title,
        start_seconds: start,
        end_seconds: end,
        aspect_ratio: aspect,
        settings,
        description: clip.description,
        target_platform: clip.target_platform,
        note: 'Edited in the ClipFoam editor',
      });
      setClip(updated);
      if (style) {
        const updatedTrack = await api.patch<CaptionTrack>(`/clips/${clipId}/captions`, {
          enabled: settings.captions_enabled,
          style,
        });
        setTrack(updatedTrack);
      }
      await refreshPreview();
      setDirty(false);
      toast({ title: 'Clip saved', description: `Revision ${updated.current_revision} recorded.`, variant: 'success' });
    } catch (err) {
      toast({ title: 'Save failed', description: errorMessage(err), variant: 'error' });
    } finally {
      setSaving(false);
    }
  }

  async function renderClip() {
    setRendering(true);
    try {
      const payload: Partial<ClipSettings> = { ...settings };
      const next = await api.post<Job>(`/clips/${clipId}/render`, { settings: payload, force: false });
      setJob(next);
      toast({ title: 'Render queued', description: 'FFmpeg will burn captions and encode the MP4.' });
    } catch (err) {
      setRendering(false);
      toast({ title: 'Could not queue render', description: errorMessage(err), variant: 'error' });
    }
  }

  async function downloadExport(exportId: string) {
    try {
      const link = await api.get<{ url: string }>(`/exports/${exportId}/download`);
      window.open(link.url, '_blank', 'noopener');
    } catch (err) {
      toast({ title: 'Download failed', description: errorMessage(err), variant: 'error' });
    }
  }

  const captionTime = Math.max(0, time - start);
  const activeLine = useMemo(
    () => preview?.lines.find((line) => captionTime >= line.start - 0.05 && captionTime <= line.end + 0.15),
    [preview?.lines, captionTime],
  );
  const warnings = preview?.warnings ?? [];
  const duration_ = Math.max(0.1, end - start);

  if (error) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" onClick={() => router.back()}>
          <ArrowLeft className="h-4 w-4" /> Back
        </Button>
        <Card>
          <CardContent className="p-8 text-center">
            <p className="text-sm font-medium">Could not open this clip</p>
            <p className="mt-1 text-xs text-muted-foreground">{error}</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (loading || !clip) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
          <Skeleton className="h-[520px] w-full" />
          <Skeleton className="h-[520px] w-full" />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <Link
          href={`/projects/${clip.project_id}/clips`}
          className="inline-flex items-center gap-1 hover:text-foreground"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> {project?.title ?? 'Project'}
        </Link>
        <span>/</span>
        <span className="truncate">{clip.title}</span>
        <span>·</span>
        <span>Revision {clip.current_revision}</span>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <Input
            value={title}
            onChange={(event) => {
              setTitle(event.target.value);
              setDirty(true);
            }}
            className="h-10 max-w-md border-transparent bg-transparent px-1 text-xl font-semibold focus-visible:border-border"
            aria-label="Clip title"
          />
          <StatusBadge status={clip.status} />
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => void saveAll()} loading={saving} disabled={!dirty}>
            <Save className="h-3.5 w-3.5" /> Save
          </Button>
          <Button size="sm" onClick={() => void renderClip()} loading={rendering}>
            <Film className="h-3.5 w-3.5" /> Render MP4
          </Button>
        </div>
      </div>

      {job ? <JobProgress job={job} /> : null}

      <div className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
        <div className="space-y-4">
          <Card className="overflow-hidden">
            <CardContent className="space-y-4 p-4">
              <div
                className="relative mx-auto overflow-hidden rounded-lg bg-black"
                style={{
                  aspectRatio: aspect.replace(':', ' / '),
                  maxHeight: '58vh',
                  width: aspect === '16:9' ? '100%' : 'auto',
                }}
              >
                {media?.stream_url ? (
                  <video
                    ref={videoRef}
                    src={media.stream_url}
                    className="h-full w-full object-cover"
                    playsInline
                    preload="metadata"
                    onClick={() => {
                      const element = videoRef.current;
                      if (!element) return;
                      if (element.paused) void element.play();
                      else element.pause();
                    }}
                    onLoadedMetadata={(event) => {
                      setDuration(event.currentTarget.duration);
                      event.currentTarget.currentTime = start;
                    }}
                    onPlay={() => setPlaying(true)}
                    onPause={() => setPlaying(false)}
                    onTimeUpdate={(event) => {
                      const current = event.currentTarget.currentTime;
                      setTime(current);
                      if (current >= end - 0.02) {
                        event.currentTarget.pause();
                        event.currentTarget.currentTime = start;
                      }
                    }}
                  />
                ) : (
                  <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
                    Source video unavailable
                  </div>
                )}
                {style && preview ? (
                  <CaptionOverlay style={style} lines={preview.lines} time={captionTime} />
                ) : null}
                {settings.overlay_text ? (
                  <div
                    className={cn(
                      'pointer-events-none absolute inset-x-0 flex justify-center px-6',
                      settings.overlay_position === 'top' ? 'top-4' : 'bottom-4',
                    )}
                  >
                    <span className="rounded bg-black/60 px-3 py-1.5 text-sm font-medium text-white">
                      {settings.overlay_text}
                    </span>
                  </div>
                ) : null}
                {!playing ? (
                  <button
                    type="button"
                    onClick={() => {
                      const element = videoRef.current;
                      if (!element) return;
                      void element.play();
                    }}
                    className="absolute inset-0 flex items-center justify-center bg-black/20 transition-colors hover:bg-black/10"
                    aria-label="Play preview"
                  >
                    <span className="flex h-14 w-14 items-center justify-center rounded-full bg-black/70 backdrop-blur">
                      <Play className="ml-0.5 h-6 w-6 text-white" />
                    </span>
                  </button>
                ) : null}
              </div>

              <div className="flex items-center gap-3">
                <Button
                  size="icon"
                  variant="secondary"
                  onClick={() => {
                    const element = videoRef.current;
                    if (!element) return;
                    if (element.paused) void element.play();
                    else element.pause();
                  }}
                  aria-label={playing ? 'Pause' : 'Play'}
                >
                  {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                </Button>
                <div className="flex-1">
                  <Slider
                    min={start}
                    max={end}
                    step={0.05}
                    value={[Math.min(Math.max(time, start), end)]}
                    onValueChange={([value]) => {
                      const element = videoRef.current;
                      if (element) element.currentTime = value;
                      setTime(value);
                    }}
                    aria-label="Playhead"
                  />
                </div>
                <span className="w-24 text-right font-mono text-xs text-muted-foreground">
                  {formatClock(captionTime)} / {formatClock(duration_)}
                </span>
                <Button
                  size="icon"
                  variant="ghost"
                  onClick={() => {
                    const element = videoRef.current;
                    if (!element) return;
                    element.currentTime = start;
                    setTime(start);
                  }}
                  aria-label="Restart preview"
                >
                  <RotateCcw className="h-4 w-4" />
                </Button>
              </div>

              {activeLine ? (
                <p className="rounded-md border border-border/70 bg-secondary/30 px-3 py-2 text-xs text-muted-foreground">
                  <span className="font-mono text-foreground">{formatClock(activeLine.start)}</span>{' '}
                  {activeLine.text}
                </p>
              ) : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Scissors className="h-4 w-4 text-primary" aria-hidden /> Trim
              </CardTitle>
              <CardDescription>Cut points are frame-accurate and stored with each revision.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-3">
                <div className="space-y-1.5">
                  <Label htmlFor="start-seconds">Start</Label>
                  <Input
                    id="start-seconds"
                    type="number"
                    min={0}
                    step={0.1}
                    value={start.toFixed(2)}
                    onChange={(event) => {
                      const value = Math.max(0, Number(event.target.value));
                      setStart(Math.min(value, end - 0.5));
                      setDirty(true);
                    }}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="end-seconds">End</Label>
                  <Input
                    id="end-seconds"
                    type="number"
                    min={0}
                    step={0.1}
                    value={end.toFixed(2)}
                    onChange={(event) => {
                      const value = Number(event.target.value);
                      setEnd(Math.max(value, start + 0.5));
                      setDirty(true);
                    }}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="aspect">Aspect ratio</Label>
                  <Select
                    value={aspect}
                    onValueChange={(value) => {
                      setAspect(value as AspectRatio);
                      setDirty(true);
                    }}
                  >
                    <SelectTrigger id="aspect"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {(['9:16', '4:5', '1:1', '16:9'] as AspectRatio[]).map((ratio) => (
                        <SelectItem key={ratio} value={ratio}>{aspectLabel(ratio)}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    const element = videoRef.current;
                    if (!element) return;
                    setStart(Number(element.currentTime.toFixed(2)));
                    setDirty(true);
                  }}
                >
                  Set start to playhead
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    const element = videoRef.current;
                    if (!element) return;
                    setEnd(Number(element.currentTime.toFixed(2)));
                    setDirty(true);
                  }}
                >
                  Set end to playhead
                </Button>
                <Badge variant="secondary" className="ml-auto">
                  {formatDuration(end - start)} · source {formatDuration(duration)}
                </Badge>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex-row items-center justify-between">
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Download className="h-4 w-4 text-primary" aria-hidden /> Exports
                </CardTitle>
                <CardDescription>Every render keeps its exact settings snapshot.</CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              {exportsList.length ? (
                <ul className="divide-y divide-border/70">
                  {exportsList.map((item) => (
                    <li key={item.id} className="flex items-center justify-between gap-3 py-2.5">
                      <div className="min-w-0">
                        <p className="text-sm font-medium">
                          {item.format.toUpperCase()} · {item.width}×{item.height}
                        </p>
                        <p className="text-[11px] text-muted-foreground">
                          {formatBytes(item.size_bytes)} · {item.fps ?? '—'} fps · {item.status}
                        </p>
                      </div>
                      {item.status === 'ready' ? (
                        <Button size="sm" variant="outline" onClick={() => void downloadExport(item.id)}>
                          Download
                        </Button>
                      ) : item.status === 'failed' ? (
                        <span className="text-[11px] text-red-300">{item.error_message?.slice(0, 60)}</span>
                      ) : (
                        <StatusBadge status={item.status} />
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="py-4 text-center text-xs text-muted-foreground">
                  No renders yet — hit “Render MP4” to burn captions into the output.
                </p>
              )}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            <CardContent className="p-4">
              <Tabs defaultValue="captions">
                <TabsList className="grid w-full grid-cols-4">
                  <TabsTrigger value="captions"><Captions className="h-3.5 w-3.5" /> Captions</TabsTrigger>
                  <TabsTrigger value="layout"><Layers className="h-3.5 w-3.5" /> Layout</TabsTrigger>
                  <TabsTrigger value="audio"><Volume2 className="h-3.5 w-3.5" /> Audio</TabsTrigger>
                  <TabsTrigger value="text"><Sparkles className="h-3.5 w-3.5" /> Copy</TabsTrigger>
                </TabsList>

                <TabsContent value="captions" className="mt-4 space-y-4">
                  <label className="flex items-center justify-between rounded-lg border border-border bg-secondary/30 px-3 py-2.5 text-sm">
                    <span className="flex items-center gap-2">
                      <Captions className="h-4 w-4 text-primary" /> Burn in captions
                    </span>
                    <Switch
                      checked={settings.captions_enabled}
                      onCheckedChange={(checked) => {
                        setSettings({ ...settings, captions_enabled: checked });
                        setDirty(true);
                      }}
                    />
                  </label>

                  {track ? (
                    <p className="text-[11px] text-muted-foreground">
                      Track v{track.version} · {preview?.lines.length ?? 0} caption lines overlap this window
                    </p>
                  ) : null}

                  {warnings.length ? (
                    <div className="space-y-1.5 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3">
                      {warnings.map((warning) => (
                        <p key={warning} className="flex gap-2 text-[11px] leading-relaxed text-amber-300">
                          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" /> {warning}
                        </p>
                      ))}
                    </div>
                  ) : null}

                  {style ? (
                    <CaptionStyleEditor
                      style={style}
                      presets={presets}
                      onChange={patchStyle}
                      onPreset={applyPreset}
                    />
                  ) : (
                    <Skeleton className="h-64 w-full" />
                  )}
                </TabsContent>

                <TabsContent value="layout" className="mt-4 space-y-4">
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1.5">
                      <Label>Resolution</Label>
                      <Select
                        value={settings.resolution}
                        onValueChange={(value) => {
                          setSettings({ ...settings, resolution: value as ClipSettings['resolution'] });
                          setDirty(true);
                        }}
                      >
                        <SelectTrigger><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {(['720p', '1080p', '1440p', '2160p'] as const).map((value) => (
                            <SelectItem key={value} value={value}>{value}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1.5">
                      <Label>Quality</Label>
                      <Select
                        value={settings.quality}
                        onValueChange={(value) => {
                          setSettings({ ...settings, quality: value as ClipSettings['quality'] });
                          setDirty(true);
                        }}
                      >
                        <SelectTrigger><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {(['draft', 'standard', 'high'] as const).map((value) => (
                            <SelectItem key={value} value={value}>{value}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1.5">
                      <Label>Reframe</Label>
                      <Select
                        value={settings.reframe_mode}
                        onValueChange={(value) => {
                          setSettings({ ...settings, reframe_mode: value as ClipSettings['reframe_mode'] });
                          setDirty(true);
                        }}
                      >
                        <SelectTrigger><SelectValue /></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="center">Center crop</SelectItem>
                          <SelectItem value="face">Face-aware</SelectItem>
                          <SelectItem value="motion">Motion-aware</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1.5">
                      <Label>Frame rate</Label>
                      <Select
                        value={String(settings.fps)}
                        onValueChange={(value) => {
                          setSettings({ ...settings, fps: Number(value) });
                          setDirty(true);
                        }}
                      >
                        <SelectTrigger><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {[24, 25, 30, 50, 60].map((value) => (
                            <SelectItem key={value} value={String(value)}>{value} fps</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>

                  <label className="flex items-center justify-between rounded-lg border border-border bg-secondary/30 px-3 py-2.5 text-sm">
                    <span>Apply brand logo</span>
                    <Switch
                      checked={settings.logo_enabled}
                      onCheckedChange={(checked) => {
                        setSettings({ ...settings, logo_enabled: checked });
                        setDirty(true);
                      }}
                    />
                  </label>
                  <p className="text-[11px] leading-relaxed text-muted-foreground">
                    Logos come from the project’s brand kit. Without an uploaded logo the renderer
                    skips the overlay instead of drawing a placeholder.
                  </p>

                  <div className="space-y-1.5">
                    <Label htmlFor="overlay-text">Persistent overlay text</Label>
                    <Input
                      id="overlay-text"
                      value={settings.overlay_text ?? ''}
                      maxLength={200}
                      onChange={(event) => {
                        setSettings({ ...settings, overlay_text: event.target.value || null });
                        setDirty(true);
                      }}
                      placeholder="@yourhandle"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Overlay position</Label>
                    <Select
                      value={settings.overlay_position}
                      onValueChange={(value) => {
                        setSettings({ ...settings, overlay_position: value as ClipSettings['overlay_position'] });
                        setDirty(true);
                      }}
                    >
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="top">Top</SelectItem>
                        <SelectItem value="bottom">Bottom</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </TabsContent>

                <TabsContent value="audio" className="mt-4 space-y-4">
                  <label className="flex items-center justify-between rounded-lg border border-border bg-secondary/30 px-3 py-2.5 text-sm">
                    <span className="flex items-center gap-2"><Volume2 className="h-4 w-4 text-primary" /> Loudness normalize</span>
                    <Switch
                      checked={settings.normalize_audio}
                      onCheckedChange={(checked) => {
                        setSettings({ ...settings, normalize_audio: checked });
                        setDirty(true);
                      }}
                    />
                  </label>
                  <label className="flex items-center justify-between rounded-lg border border-border bg-secondary/30 px-3 py-2.5 text-sm">
                    <span>Remove silences</span>
                    <Switch
                      checked={settings.remove_silence}
                      onCheckedChange={(checked) => {
                        setSettings({ ...settings, remove_silence: checked });
                        setDirty(true);
                      }}
                    />
                  </label>
                  {settings.remove_silence ? (
                    <div className="space-y-1.5">
                      <div className="flex items-center justify-between">
                        <Label htmlFor="silence-min">Minimum silence</Label>
                        <span className="font-mono text-xs text-muted-foreground">
                          {settings.silence_min_seconds.toFixed(1)}s
                        </span>
                      </div>
                      <Slider
                        id="silence-min"
                        min={0.2}
                        max={3}
                        step={0.1}
                        value={[settings.silence_min_seconds]}
                        onValueChange={([value]) => {
                          setSettings({ ...settings, silence_min_seconds: value });
                          setDirty(true);
                        }}
                      />
                    </div>
                  ) : null}
                  <label className="flex items-center justify-between rounded-lg border border-border bg-secondary/30 px-3 py-2.5 text-sm">
                    <span>Remove filler words</span>
                    <Switch
                      checked={settings.remove_filler_words}
                      onCheckedChange={(checked) => {
                        setSettings({ ...settings, remove_filler_words: checked });
                        setDirty(true);
                      }}
                    />
                  </label>
                  <div className="space-y-1.5">
                    <Label>Background music</Label>
                    <Select
                      value={settings.background_music}
                      onValueChange={(value) => {
                        setSettings({ ...settings, background_music: value as ClipSettings['background_music'] });
                        setDirty(true);
                      }}
                    >
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">None</SelectItem>
                        <SelectItem value="ambient_soft">Ambient soft</SelectItem>
                        <SelectItem value="upbeat">Upbeat</SelectItem>
                        <SelectItem value="cinematic">Cinematic</SelectItem>
                      </SelectContent>
                    </Select>
                    <p className="text-[11px] leading-relaxed text-amber-300/90">
                      No music library ships with ClipFoam, so music selections are reported as
                      unavailable by the renderer rather than silently ignored. Add your own track
                      to the media library to enable it.
                    </p>
                  </div>
                </TabsContent>

                <TabsContent value="text" className="mt-4 space-y-4">
                  <div className="space-y-1.5">
                    <Label htmlFor="clip-desc">Description</Label>
                    <Textarea
                      id="clip-desc"
                      value={clip.description ?? ''}
                      onChange={(event) => {
                        setClip({ ...clip, description: event.target.value });
                        setDirty(true);
                      }}
                      placeholder="Internal notes about this clip"
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1.5">
                      <Label>Target platform</Label>
                      <Select
                        value={clip.target_platform ?? 'tiktok'}
                        onValueChange={(value) => {
                          setClip({ ...clip, target_platform: value });
                          setDirty(true);
                        }}
                      >
                        <SelectTrigger><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {Object.entries(PLATFORM_LABELS).map(([value, label]) => (
                            <SelectItem key={value} value={value}>{label}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1.5">
                      <Label>Intro / outro</Label>
                      <Input
                        value={settings.intro_text ?? ''}
                        placeholder="Intro card text"
                        onChange={(event) => {
                          setSettings({ ...settings, intro_text: event.target.value || null });
                          setDirty(true);
                        }}
                      />
                    </div>
                  </div>
                  <div className="rounded-lg border border-border bg-secondary/30 p-3">
                    <p className="flex items-center gap-2 text-xs font-medium">
                      <Wand2 className="h-3.5 w-3.5 text-primary" /> Generate post copy
                    </p>
                    <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                      Titles, hooks, hashtags and descriptions are generated from this clip’s transcript
                      in the Content Studio — with a clearly labelled deterministic fallback when no
                      language-model provider is configured.
                    </p>
                    <Button asChild size="sm" variant="secondary" className="mt-2 w-full">
                      <Link href="/content-studio">Open Content Studio</Link>
                    </Button>
                  </div>
                </TabsContent>
              </Tabs>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="space-y-2 p-4 text-[11px] text-muted-foreground">
              <p className="flex items-center gap-2 text-xs font-medium text-foreground">
                <Gauge className="h-3.5 w-3.5 text-primary" /> Render honesty
              </p>
              <p className="leading-relaxed">
                Previews are drawn in the browser from the same caption lines the server will burn in.
                The final MP4 is produced by FFmpeg with libass; per-word timing, wrapping and safe
                areas can differ by a few pixels.
              </p>
              <p className="leading-relaxed">
                {activeLine ? (
                  <>
                    <Check className="mr-1 inline h-3 w-3 text-primary" />
                    Captions cover the current clip window.
                  </>
                ) : (
                  <>
                    <Loader2 className="mr-1 inline h-3 w-3" />
                    No active caption at this playhead position.
                  </>
                )}
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
