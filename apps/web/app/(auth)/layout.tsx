import Link from 'next/link';

import { Logo } from '@/components/shared/logo';
import { ThemeToggle } from '@/components/shared/theme-toggle';

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-dvh lg:grid-cols-2">
      <div className="relative hidden flex-col justify-between border-r border-border/70 bg-secondary/40 p-10 lg:flex">
        <div className="pointer-events-none absolute inset-0 surface-grid opacity-30" aria-hidden />
        <div className="relative">
          <Logo />
        </div>
        <div className="relative max-w-md space-y-4">
          <h1 className="text-3xl font-semibold leading-tight tracking-tight">
            One workspace from raw footage to <span className="text-primary">scheduled post</span>.
          </h1>
          <p className="text-sm leading-relaxed text-muted-foreground">
            ClipFoam keeps every stage honest and inspectable: real transcripts, real timestamps,
            real FFmpeg renders, and clearly labelled deterministic fallbacks when no AI provider
            is configured.
          </p>
          <ul className="space-y-2 text-sm text-muted-foreground">
            <li>· Local-first transcription and rendering</li>
            <li>· Durable background jobs with progress streams</li>
            <li>· Private media with signed, expiring URLs</li>
          </ul>
        </div>
        <p className="relative text-xs text-muted-foreground">
          Open source · Self-hostable · MIT licensed
        </p>
      </div>
      <div className="flex flex-col">
        <div className="flex items-center justify-between px-6 py-5 lg:px-10">
          <div className="lg:hidden">
            <Logo />
          </div>
          <div className="ml-auto">
            <ThemeToggle />
          </div>
        </div>
        <div className="flex flex-1 items-center justify-center px-6 pb-16 lg:px-10">
          <div className="w-full max-w-sm">{children}</div>
        </div>
        <div className="px-6 pb-6 text-center text-xs text-muted-foreground lg:px-10">
          <Link href="/" className="hover:text-foreground">
            ← Back to home
          </Link>
        </div>
      </div>
    </div>
  );
}
