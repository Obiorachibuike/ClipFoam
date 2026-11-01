'use client';

import { useEffect, useMemo, useState } from 'react';
import { Captions, Check, FileText, Loader2, Pencil, Play, Upload, X } from 'lucide-react';

import { EmptyState } from '@/components/shared/empty-state';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { api, errorMessage } from '@/lib/api';
import { formatClock } from '@/lib/format';
import { toast } from '@/lib/store';
import type { Job, MediaAsset, Transcript, TranscriptSegment } from '@/lib/types';
import { cn } from '@/lib/utils';

export function TranscriptPanel({
  media,
  transcript,
  loading,
  onRefresh,
  onJobStarted,
  onSeek,
}: {
  media: MediaAsset | null;
  transcript: Transcript | null;
  loading: boolean;
  onRefresh: () => void;
  onJobStarted: (jobId: string) => void;
  onSeek?: (seconds: number) => void;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [starting, setStarting] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState('');
  const [importFormat, setImportFormat] = useState<'srt' | 'vtt' | 'json'>('srt');
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');

  const segments = useMemo(() => {
    if (!transcript) return [];
    if (!query.trim()) return transcript.segments;
    const needle = query.toLowerCase();
    return transcript.segments.filter((segment) => segment.text.toLowerCase().includes(needle));
  }, [transcript, query]);

  useEffect(() => {
    setEditingId(null);
  }, [transcript?.id]);

  async function startTranscription() {
    if (!media) return;
    setStarting(true);
    setError(null);
    try {
      const job = await api.post<Job>(`/media/${media.id}/transcribe`, { language: null, force: false });
      toast({ title: 'Transcription queued', description: 'Running the configured engine…' });
      onJobStarted(job.id);
    } catch (err) {
      const message = errorMessage(err);
      setError(message);
      toast({ title: 'Could not start transcription', description: message, variant: 'error' });
    } finally {
      setStarting(false);
    }
  }

  async function saveSegment(segment: TranscriptSegment) {
    if (!transcript) return;
    setSaving(true);
    try {
      await api.patch(`/transcripts/${transcript.id}/segments/${segment.id}`, { text: draft });
      toast({ title: 'Segment updated', description: 'The original transcription is preserved.', variant: 'success' });
      setEditingId(null);
      onRefresh();
    } catch (err) {
      toast({ title: 'Update failed', description: errorMessage(err), variant: 'error' });
    } finally {
      setSaving(false);
    }
  }

  async function submitImport(event: React.FormEvent) {
    event.preventDefault();
    if (!media) return;
    setImporting(true);
    setError(null);
    try {
      await api.post<Transcript>(`/transcripts/import?media_id=${media.id}`, {
        content: importText,
        format: importFormat,
      });
      toast({ title: 'Transcript imported', description: 'Captions and highlights can now use these timestamps.', variant: 'success' });
      setImportOpen(false);
      setImportText('');
      onRefresh();
    } catch (err) {
      const message = errorMessage(err);
      setError(message);
      toast({ title: 'Import failed', description: message, variant: 'error' });
    } finally {
      setImporting(false);
    }
  }

  if (!media) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Transcript</CardTitle>
          <CardDescription>Upload source media to transcribe it.</CardDescription>
        </CardHeader>
        <CardContent>
          <EmptyState icon={Captions} title="No media yet" description="Transcripts are generated from uploaded video or audio." />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader className="gap-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Captions className="h-4 w-4 text-primary" aria-hidden /> Transcript
            </CardTitle>
            <CardDescription>
              {transcript?.status === 'ready'
                ? `${transcript.segments.length} cues · ${transcript.word_count} words · ${
                    transcript.is_manual ? 'imported' : transcript.provider
                  }${transcript.language ? ` · ${transcript.language}` : ''}`
                : 'Word-level timestamps power captions, highlights and transcript-based trimming.'}
            </CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
            {transcript?.status !== 'ready' ? (
              <Button size="sm" onClick={() => void startTranscription()} loading={starting}>
                <Play className="h-3.5 w-3.5" /> Transcribe
              </Button>
            ) : (
              <Button size="sm" variant="secondary" onClick={() => void startTranscription()} loading={starting}>
                <Loader2 className="h-3.5 w-3.5" /> Re-run
              </Button>
            )}
            <Dialog open={importOpen} onOpenChange={setImportOpen}>
              <DialogTrigger asChild>
                <Button size="sm" variant="outline">
                  <Upload className="h-3.5 w-3.5" /> Import cues
                </Button>
              </DialogTrigger>
              <DialogContent className="max-w-2xl">
                <DialogHeader>
                  <DialogTitle>Import an existing transcript</DialogTitle>
                  <DialogDescription>
                    Paste SRT, VTT or JSON cues. Useful when you already have a transcript or when
                    the local Whisper model is unavailable.
                  </DialogDescription>
                </DialogHeader>
                <form onSubmit={submitImport} className="space-y-3">
                  <div className="flex gap-2">
                    {(['srt', 'vtt', 'json'] as const).map((format) => (
                      <Button
                        key={format}
                        type="button"
                        size="sm"
                        variant={importFormat === format ? 'default' : 'outline'}
                        onClick={() => setImportFormat(format)}
                      >
                        {format.toUpperCase()}
                      </Button>
                    ))}
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="cue-body">Cue content</Label>
                    <Textarea
                      id="cue-body"
                      required
                      value={importText}
                      onChange={(event) => setImportText(event.target.value)}
                      className="min-h-[220px] font-mono text-xs"
                      placeholder={'1\n00:00:00,000 --> 00:00:04,000\nYour first line of speech.'}
                    />
                  </div>
                  <DialogFooter>
                    <Button type="submit" loading={importing}>Import transcript</Button>
                  </DialogFooter>
                </form>
              </DialogContent>
            </Dialog>
          </div>
        </div>
        {transcript?.status === 'ready' ? (
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search the transcript…"
            aria-label="Search transcript"
            className="h-9"
          />
        ) : null}
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="space-y-2">
            {Array.from({ length: 5 }).map((_, index) => (
              <Skeleton key={index} className="h-12 w-full" />
            ))}
          </div>
        ) : transcript?.status === 'failed' ? (
          <div className="rounded-lg border border-red-500/30 bg-red-500/5 p-4 text-sm">
            <p className="font-medium text-red-300">Transcription failed</p>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{transcript.error_message}</p>
            <p className="mt-2 text-xs text-muted-foreground">
              You can still work on this project by importing an SRT/VTT transcript.
            </p>
          </div>
        ) : transcript?.status === 'ready' ? (
          <div className="max-h-[420px] space-y-1 overflow-y-auto pr-1">
            {segments.map((segment) => (
              <div
                key={segment.id}
                className={cn(
                  'group rounded-lg border border-transparent px-2.5 py-2 transition-colors hover:border-border hover:bg-secondary/40',
                  editingId === segment.id && 'border-border bg-secondary/40',
                )}
              >
                <div className="flex items-start gap-3">
                  <button
                    type="button"
                    onClick={() => onSeek?.(segment.start_seconds)}
                    className="mt-0.5 shrink-0 rounded font-mono text-[11px] text-muted-foreground transition-colors hover:text-primary"
                    title="Jump to this cue"
                  >
                    {formatClock(segment.start_seconds)}
                  </button>
                  <div className="min-w-0 flex-1">
                    {editingId === segment.id ? (
                      <div className="space-y-2">
                        <Textarea
                          value={draft}
                          onChange={(event) => setDraft(event.target.value)}
                          className="min-h-[64px] text-sm"
                          autoFocus
                        />
                        <div className="flex gap-2">
                          <Button size="sm" onClick={() => void saveSegment(segment)} loading={saving}>
                            <Check className="h-3.5 w-3.5" /> Save
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => setEditingId(null)}>
                            <X className="h-3.5 w-3.5" /> Cancel
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <p className="text-sm leading-relaxed">
                        {segment.text}
                        {segment.is_edited ? (
                          <Badge variant="muted" className="ml-2 align-middle text-[10px]">
                            edited
                          </Badge>
                        ) : null}
                      </p>
                    )}
                    {editingId !== segment.id && segment.original_text !== segment.text ? (
                      <p className="mt-1 text-[11px] text-muted-foreground line-through">{segment.original_text}</p>
                    ) : null}
                  </div>
                  {editingId !== segment.id ? (
                    <Button
                      size="icon"
                      variant="ghost"
                      className="opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
                      onClick={() => {
                        setEditingId(segment.id);
                        setDraft(segment.text);
                      }}
                      aria-label="Edit this cue"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                  ) : null}
                </div>
              </div>
            ))}
            {segments.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">No cues match “{query}”.</p>
            ) : null}
          </div>
        ) : (
          <EmptyState
            icon={FileText}
            title="No transcript yet"
            description="Run local transcription, or import an existing SRT/VTT file to unlock captions and highlight detection."
          />
        )}
        {error ? <p className="mt-3 rounded-md bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</p> : null}
        {transcript?.status === 'ready' ? (
          <div className="mt-4 flex flex-wrap gap-2 border-t border-border/70 pt-4 text-xs">
            {(['srt', 'vtt', 'json'] as const).map((format) => (
              <a
                key={format}
                href={`/api/v1/media/${media.id}/transcript/export?format=${format}`}
                className="rounded-md border border-border px-2.5 py-1.5 text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground"
              >
                Download {format.toUpperCase()}
              </a>
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
