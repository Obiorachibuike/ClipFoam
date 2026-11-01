import Link from 'next/link';
import {
  ArrowRight,
  AudioLines,
  Captions,
  Clapperboard,
  CloudUpload,
  Github,
  Layers,
  Scissors,
  Share2,
  Sparkles,
  Wand2,
} from 'lucide-react';

import { Logo } from '@/components/shared/logo';
import { ThemeToggle } from '@/components/shared/theme-toggle';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';

const FEATURES = [
  {
    icon: AudioLines,
    title: 'Transcription you can edit',
    body: 'faster-whisper runs locally, keeps word-level timestamps, and preserves the original text beside every edit you make.',
  },
  {
    icon: Sparkles,
    title: 'Highlights grounded in speech',
    body: 'A deterministic scorer ranks pauses, topic shifts and hook strength. Optional local models add semantic analysis — always validated against the real transcript.',
  },
  {
    icon: Scissors,
    title: 'Transcript-based editing',
    body: 'Trim by selecting sentences, strip silences, remove filler words, and keep a full revision history per clip.',
  },
  {
    icon: Captions,
    title: 'Eight caption styles',
    body: 'Karaoke, bold creator, minimal, boxed, podcast, neon, classic and editorial presets rendered with libass to match your preview.',
  },
  {
    icon: Clapperboard,
    title: 'Reframe for every platform',
    body: 'Portrait, square and landscape outputs with optional face-aware panning that falls back to a clean centre crop.',
  },
  {
    icon: Share2,
    title: 'Publish where the API allows',
    body: 'OAuth to YouTube and Mastodon with durable publish jobs, duplicate protection and honest per-platform limitations.',
  },
];

const STEPS = [
  { title: 'Ingest', body: 'Upload a file or import an authorized direct URL. Metadata, thumbnails and limits are validated with FFprobe.' },
  { title: 'Understand', body: 'Transcribe, correct the transcript, then score candidate windows against duration and overlap rules.' },
  { title: 'Craft', body: 'Reframe, restyle captions, add overlays and brand kits. Every render snapshots its configuration.' },
  { title: 'Ship', body: 'Download MP4/SRT or schedule a post. Publishing state lives in the database, not in a browser tab.' },
];

export default function LandingPage() {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-40 border-b border-border/70 bg-background/85 backdrop-blur">
        <div className="container flex h-16 items-center justify-between">
          <Logo />
          <nav className="hidden items-center gap-6 text-sm text-muted-foreground md:flex" aria-label="Main">
            <a href="#features" className="transition-colors hover:text-foreground">Features</a>
            <a href="#workflow" className="transition-colors hover:text-foreground">Workflow</a>
            <a href="#selfhost" className="transition-colors hover:text-foreground">Self-hosting</a>
            <a
              href="https://github.com/Obiorachibuike/ClipFoam"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 transition-colors hover:text-foreground"
            >
              <Github className="h-4 w-4" /> GitHub
            </a>
          </nav>
          <div className="flex items-center gap-1.5">
            <ThemeToggle />
            <Button asChild variant="ghost" size="sm" className="hidden sm:inline-flex">
              <Link href="/login">Sign in</Link>
            </Button>
            <Button asChild size="sm">
              <Link href="/register">
                Start free <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </Button>
          </div>
        </div>
      </header>

      <main className="flex-1">
        <section className="relative overflow-hidden border-b border-border/60">
          <div className="pointer-events-none absolute inset-0 surface-grid opacity-40" aria-hidden />
          <div className="container relative grid gap-12 py-16 lg:grid-cols-[1.05fr_0.95fr] lg:py-24">
            <div className="flex flex-col justify-center gap-6">
              <Badge variant="secondary" className="w-fit gap-2 border-border/80 px-3 py-1">
                <span className="h-1.5 w-1.5 rounded-full bg-primary" />
                Open source · Local-first · Self-hostable
              </Badge>
              <h1 className="max-w-2xl text-balance text-4xl font-semibold leading-[1.08] tracking-tight sm:text-5xl lg:text-6xl">
                Turn long videos into <span className="text-primary">short-form gold</span>.
              </h1>
              <p className="max-w-xl text-base leading-relaxed text-muted-foreground sm:text-lg">
                ClipFoam is a complete video repurposing studio. Transcribe, find the moments worth
                posting, reframe and caption them, render with FFmpeg, and schedule the result — all
                from one workspace you can run yourself.
              </p>
              <div className="flex flex-wrap items-center gap-3">
                <Button asChild size="lg">
                  <Link href="/register">
                    Create your workspace <ArrowRight className="h-4 w-4" />
                  </Link>
                </Button>
                <Button asChild size="lg" variant="outline">
                  <Link href="/login">I already have an account</Link>
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                No paid AI key required for the core workflow: upload → transcribe → clip → captions → MP4.
              </p>
            </div>

            <Card className="relative overflow-hidden p-5 lg:p-6">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="h-2.5 w-2.5 rounded-full bg-red-500/70" />
                  <span className="h-2.5 w-2.5 rounded-full bg-amber-500/70" />
                  <span className="h-2.5 w-2.5 rounded-full bg-emerald-500/70" />
                </div>
                <span className="mono-label">clip.render · ffmpeg</span>
              </div>
              <div className="mt-4 grid gap-3">
                <div className="relative overflow-hidden rounded-lg border border-border bg-black/60 p-4">
                  <div className="absolute inset-x-0 bottom-6 flex justify-center">
                    <p className="text-center text-2xl font-extrabold uppercase leading-tight tracking-tight">
                      <span className="text-white">THE BIGGEST</span>
                      <br />
                      <span className="text-primary">MISTAKE</span>
                    </p>
                  </div>
                  <div className="flex h-44 items-end gap-[3px] pb-1" aria-hidden>
                    {[18, 34, 26, 48, 62, 40, 55, 72, 44, 30, 52, 66, 38, 24, 46, 58, 33, 21, 42, 56, 29, 36, 50, 63, 41, 27, 45, 57, 31, 22, 39, 51, 60, 35, 25, 43, 54, 32, 20, 47].map(
                      (height, index) => (
                        <span
                          key={index}
                          className="flex-1 rounded-sm bg-primary/50"
                          style={{ height: `${height}%` }}
                        />
                      ),
                    )}
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  {[
                    { label: 'Transcript', value: '5 cues · 23s' },
                    { label: 'Candidates', value: '3 ranked' },
                    { label: 'Reframe', value: '9:16 · face' },
                    { label: 'Export', value: '1080p · CRF 18' },
                  ].map((item) => (
                    <div key={item.label} className="rounded-lg border border-border bg-secondary/40 px-3 py-2.5">
                      <p className="mono-label">{item.label}</p>
                      <p className="mt-0.5 text-sm font-medium">{item.value}</p>
                    </div>
                  ))}
                </div>
              </div>
            </Card>
          </div>
        </section>

        <section id="features" className="border-b border-border/60 py-16 lg:py-20">
          <div className="container">
            <div className="max-w-2xl">
              <p className="mono-label">What is inside</p>
              <h2 className="mt-2 text-3xl font-semibold tracking-tight">A real pipeline, not a mockup</h2>
              <p className="mt-3 text-muted-foreground">
                Every stage writes to PostgreSQL, runs in a durable worker, and can be retried or
                cancelled from the UI. Nothing is faked: if a provider is missing, ClipFoam says so.
              </p>
            </div>
            <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {FEATURES.map((feature) => (
                <Card key={feature.title} className="p-5 transition-colors hover:border-primary/40">
                  <feature.icon className="h-5 w-5 text-primary" aria-hidden />
                  <h3 className="mt-3 font-medium">{feature.title}</h3>
                  <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{feature.body}</p>
                </Card>
              ))}
            </div>
          </div>
        </section>

        <section id="workflow" className="border-b border-border/60 py-16 lg:py-20">
          <div className="container">
            <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
              <div className="max-w-xl">
                <p className="mono-label">The loop</p>
                <h2 className="mt-2 text-3xl font-semibold tracking-tight">Four stages, fully inspectable</h2>
              </div>
              <p className="max-w-md text-sm text-muted-foreground">
                Job state, timings, render configuration and provider errors are persisted and visible
                in the app — no black boxes.
              </p>
            </div>
            <ol className="mt-10 grid gap-4 md:grid-cols-2 lg:grid-cols-4">
              {STEPS.map((step, index) => (
                <li key={step.title} className="relative rounded-xl border border-border bg-card p-5">
                  <span className="font-mono text-xs text-primary">0{index + 1}</span>
                  <h3 className="mt-2 font-medium">{step.title}</h3>
                  <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{step.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section id="selfhost" className="py-16 lg:py-20">
          <div className="container grid gap-10 lg:grid-cols-2 lg:items-center">
            <div className="space-y-4">
              <p className="mono-label">Deployment</p>
              <h2 className="text-3xl font-semibold tracking-tight">Run it on your own hardware</h2>
              <p className="text-muted-foreground">
                Next.js frontend, FastAPI backend, a durable Python worker, PostgreSQL, Redis (optional)
                and S3-compatible storage — wired up in the included Docker Compose stack. The local
                profile runs without any paid API keys.
              </p>
              <ul className="grid gap-2 text-sm text-muted-foreground">
                {[
                  'PostgreSQL + Alembic migrations',
                  'Private buckets with signed URLs',
                  'Durable jobs with retries, leases and cancellation',
                  'Per-workspace quota, usage and audit records',
                ].map((item) => (
                  <li key={item} className="flex items-start gap-2">
                    <Layers className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />
                    {item}
                  </li>
                ))}
              </ul>
              <div className="flex flex-wrap gap-2 pt-2">
                <Button asChild>
                  <Link href="/register">
                    <CloudUpload className="h-4 w-4" /> Open the studio
                  </Link>
                </Button>
                <Button asChild variant="outline">
                  <a href="https://github.com/Obiorachibuike/ClipFoam#readme" target="_blank" rel="noreferrer">
                    Read the docs
                  </a>
                </Button>
              </div>
            </div>
            <Card className="overflow-hidden p-0">
              <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
                <span className="mono-label">docker compose up</span>
                <Wand2 className="h-3.5 w-3.5 text-primary" aria-hidden />
              </div>
              <pre className="overflow-x-auto p-4 font-mono text-[12.5px] leading-relaxed text-muted-foreground">
{`services:
  web:      next.js  → :3000
  api:      fastapi  → :8000
  worker:   python -m app.workers.worker
  db:       postgres:16
  redis:    redis:7        (optional cache)
  minio:    s3-compatible  (optional)

$ make migrate     # alembic upgrade head
$ make dev         # api + worker + web`}
              </pre>
            </Card>
          </div>
        </section>
      </main>

      <footer className="border-t border-border/60 py-8">
        <div className="container flex flex-col items-start justify-between gap-4 sm:flex-row sm:items-center">
          <div className="flex items-center gap-3">
            <Logo />
            <span className="text-xs text-muted-foreground">Turn long videos into short-form gold.</span>
          </div>
          <div className="flex items-center gap-4 text-xs text-muted-foreground">
            <Link href="/login" className="hover:text-foreground">Sign in</Link>
            <Link href="/register" className="hover:text-foreground">Create account</Link>
            <a
              href="https://github.com/Obiorachibuike/ClipFoam"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 hover:text-foreground"
            >
              <Github className="h-3.5 w-3.5" /> Source
            </a>
          </div>
        </div>
      </footer>
    </div>
  );
}
