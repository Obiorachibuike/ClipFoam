'use client';

import Link from 'next/link';
import { useState } from 'react';
import { Check, Clock, Film, Loader2, Sparkles, Trash2, Wand2, X } from 'lucide-react';

import { EmptyState } from '@/components/shared/empty-state';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { api, errorMessage } from '@/lib/api';
import { formatClock, formatDuration } from '@/lib/format';
import { toast } from '@/lib/store';
import type { Clip, ClipCandidate, Job } from '@/lib/types';
import { cn } from '@/lib/utils';

const SCORE_PARTS: { key: string; label: string }[] = [
  { key: 'hook', label: 'Hook' },
  { key: 'salience', label: 'Salience' },
  { key: 'boundary', label: 'Boundaries' },
  { key: 'length_fit', label: 'Length fit' },
  { key: 'density', label: 'Speech density' },
  { key: 'cohesion', label: 'Cohesion' },
];

export function CandidatesPanel({
  projectId,
  candidates,
  clips,
  loading,
  transcriptReady,
  aiConfigured,
  aiLabel,
  onJobStarted,
  onRefresh,
  onSeek,
}: {
  projectId: string;
  candidates: ClipCandidate[];
  clips: Clip[];
  loading: boolean;
  transcriptReady: boolean;
  aiConfigured: boolean;
  aiLabel: string;
  onJobStarted: (jobId: string) => void;
  onRefresh: () => void;
  onSeek?: (seconds: number) => void;
}) {
  const [clipCount, setClipCount] = useState('5');
  const [minDuration, setMinDuration] = useState('20');
  const [maxDuration, setMaxDuration] = useState('75');
  const [useAi, setUseAi] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const approvedClipFor = (candidateId: string) =>
    clips.find((clip) => clip.candidate_id === candidateId);

  async function generate(event: React.FormEvent) {
    event.preventDefault();
    setGenerating(true);
    setError(null);
    try {
      const job = await api.post<{ job: Job }>(`/projects/${projectId}/candidates/generate`, {
        clip_count: Number(clipCount),
        min_duration: Number(minDuration),
        max_duration: Number(maxDuration),
        use_ai: useAi,
      });
      toast({ title: 'Highlight analysis queued', description: useAi ? 'Model-assisted ranking requested.' : 'Deterministic ranking requested.' });
      onJobStarted(job.job.id);
    } catch (err) {
      const message = errorMessage(err);
      setError(message);
      toast({ title: 'Could not start analysis', description: message, variant: 'error' });
    } finally {
      setGenerating(false);
    }
  }

  async function approve(candidate: ClipCandidate) {
    setBusyId(candidate.id);
    try {
      const clip = await api.post<Clip>(`/candidates/${candidate.id}/approve`);
      toast({ title: 'Clip created', description: `“${clip.title}” is ready to edit.`, variant: 'success' });
      onRefresh();
    } catch (err) {
      toast({ title: 'Could not create clip', description: errorMessage(err), variant: 'error' });
    } finally {
      setBusyId(null);
    }
  }

  async function reject(candidate: ClipCandidate) {
    setBusyId(candidate.id);
    try {
      await api.post(`/candidates/${candidate.id}/reject`);
      toast({ title: 'Candidate rejected' });
      onRefresh();
    } catch (err) {
      toast({ title: 'Could not reject candidate', description: errorMessage(err), variant: 'error' });
    } finally {
      setBusyId(null);
    }
  }

  async function rerank() {
    setBusyId('rerank');
    try {
      await api.post(`/projects/${projectId}/candidates/rerank`);
      toast({ title: 'Candidates re-ranked by score' });
      onRefresh();
    } catch (err) {
      toast({ title: 'Re-rank failed', description: errorMessage(err), variant: 'error' });
    } finally {
      setBusyId(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-primary" aria-hidden /> Highlight candidates
        </CardTitle>
        <CardDescription>
          Ranked from real transcript windows. Deterministic scoring is always available; model-assisted
          analysis is optional and clearly labelled.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <form onSubmit={generate} className="grid gap-3 rounded-lg border border-border bg-secondary/30 p-4 sm:grid-cols-4">
          <div className="space-y-1.5">
            <Label htmlFor="clip-count">Clips</Label>
            <Input
              id="clip-count"
              type="number"
              min={1}
              max={30}
              value={clipCount}
              onChange={(event) => setClipCount(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="min-duration">Min seconds</Label>
            <Input
              id="min-duration"
              type="number"
              min={5}
              max={600}
              value={minDuration}
              onChange={(event) => setMinDuration(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="max-duration">Max seconds</Label>
            <Input
              id="max-duration"
              type="number"
              min={10}
              max={900}
              value={maxDuration}
              onChange={(event) => setMaxDuration(event.target.value)}
            />
          </div>
          <div className="flex flex-col justify-end gap-2">
            <Button type="submit" loading={generating} disabled={!transcriptReady}>
              <Wand2 className="h-4 w-4" /> Detect highlights
            </Button>
          </div>
          <div className="sm:col-span-4 flex flex-wrap items-center justify-between gap-3">
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <Switch
                checked={useAi && aiConfigured}
                disabled={!aiConfigured}
                onCheckedChange={setUseAi}
                aria-label="Use language model for candidate analysis"
              />
              Use model-assisted analysis
              <span className="text-muted-foreground/70">({aiLabel})</span>
            </label>
            {!transcriptReady ? (
              <span className="text-xs text-amber-400">A ready transcript is required first.</span>
            ) : null}
          </div>
        </form>
        {error ? <p className="rounded-md bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</p> : null}

        {loading ? (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, index) => (
              <Skeleton key={index} className="h-32 w-full" />
            ))}
          </div>
        ) : candidates.length ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <p className="text-xs text-muted-foreground">{candidates.length} candidate(s)</p>
              <Button variant="ghost" size="sm" onClick={() => void rerank()} loading={busyId === 'rerank'}>
                Re-rank
              </Button>
            </div>
            {candidates.map((candidate) => {
              const clip = approvedClipFor(candidate.id);
              return (
                <div
                  key={candidate.id}
                  className={cn(
                    'rounded-xl border p-4 transition-colors',
                    candidate.status === 'approved' || clip
                      ? 'border-primary/40 bg-primary/[0.04]'
                      : candidate.status === 'rejected'
                        ? 'border-border/60 bg-secondary/20 opacity-70'
                        : 'border-border bg-card/60',
                  )}
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant="default">#{candidate.rank}</Badge>
                        <Badge variant="secondary" className="font-mono text-[10px] uppercase">
                          {candidate.algorithm === 'ai_assisted' ? 'AI-assisted' : 'Deterministic'}
                        </Badge>
                        {candidate.category ? <Badge variant="muted">{candidate.category}</Badge> : null}
                        <button
                          type="button"
                          onClick={() => onSeek?.(candidate.start_seconds)}
                          className="inline-flex items-center gap-1 font-mono text-[11px] text-muted-foreground hover:text-primary"
                        >
                          <Clock className="h-3 w-3" />
                          {formatClock(candidate.start_seconds)}–{formatClock(candidate.end_seconds)}
                        </button>
                        <span className="text-[11px] text-muted-foreground">
                          {formatDuration(candidate.end_seconds - candidate.start_seconds)} long
                        </span>
                      </div>
                      <p className="mt-2 text-sm font-medium">{candidate.suggested_title}</p>
                      <p className="mt-1 line-clamp-3 text-xs leading-relaxed text-muted-foreground">{candidate.excerpt}</p>
                    </div>
                    <div className="flex flex-col items-end gap-2">
                      <div className="text-right">
                        <p className="text-lg font-semibold tabular-nums text-primary">{candidate.score.toFixed(2)}</p>
                        <p className="mono-label">score</p>
                      </div>
                      <div className="flex gap-1.5">
                        {clip ? (
                          <Button asChild size="sm" variant="secondary">
                            <Link href={`/editor/${clip.id}`}>
                              <Film className="h-3.5 w-3.5" /> Edit clip
                            </Link>
                          </Button>
                        ) : (
                          <Button
                            size="sm"
                            onClick={() => void approve(candidate)}
                            loading={busyId === candidate.id}
                            disabled={candidate.status === 'rejected'}
                          >
                            <Check className="h-3.5 w-3.5" /> Approve
                          </Button>
                        )}
                        {candidate.status !== 'rejected' && !clip ? (
                          <Button
                            size="icon"
                            variant="ghost"
                            onClick={() => void reject(candidate)}
                            disabled={busyId === candidate.id}
                            aria-label="Reject candidate"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        ) : null}
                      </div>
                    </div>
                  </div>

                  <div className="mt-3 grid gap-2 border-t border-border/60 pt-3 sm:grid-cols-2">
                    <div className="flex flex-wrap gap-x-3 gap-y-1">
                      {SCORE_PARTS.map((part) => {
                        const value = Number(candidate.score_breakdown[part.key] ?? 0);
                        return (
                          <span key={part.key} className="text-[11px] text-muted-foreground">
                            {part.label}{' '}
                            <span className="font-mono text-foreground/80">{value.toFixed(2)}</span>
                          </span>
                        );
                      })}
                    </div>
                    {candidate.explanation ? (
                      <p className="text-[11px] leading-relaxed text-muted-foreground sm:text-right">
                        {candidate.explanation}
                      </p>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <EmptyState
            icon={Sparkles}
            title="No candidates yet"
            description="Run highlight detection to score transcript windows against your clip length and duration limits."
          />
        )}
      </CardContent>
    </Card>
  );
}

export function CandidateLoadingNote() {
  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground">
      <Loader2 className="h-3.5 w-3.5 animate-spin" /> Analysing transcript…
    </div>
  );
}

export function useCandidateDefaults() {
  const [settings] = useState({ clipCount: '5', min: '20', max: '75' });
  return settings;
}

export { X };
