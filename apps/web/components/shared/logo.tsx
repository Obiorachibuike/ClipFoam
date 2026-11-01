import Link from 'next/link';

import { cn } from '@/lib/utils';

export function Logo({ className, href = '/' }: { className?: string; href?: string }) {
  return (
    <Link href={href} className={cn('group inline-flex items-center gap-2.5', className)} aria-label="ClipFoam home">
      <span className="relative flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
        <svg viewBox="0 0 24 24" className="h-4.5 w-4.5" width="18" height="18" aria-hidden>
          <path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v11a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 17.5z" fill="currentColor" opacity="0.25" />
          <path d="M10 8.6c0-.5.55-.82.98-.56l4.6 2.9a.65.65 0 0 1 0 1.12l-4.6 2.9A.65.65 0 0 1 10 14.4z" fill="currentColor" />
        </svg>
      </span>
      <span className="text-[15px] font-semibold tracking-tight">
        Clip<span className="text-primary">Foam</span>
      </span>
    </Link>
  );
}
