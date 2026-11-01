import { Badge } from '@/components/ui/badge';

const MAP: Record<string, { label: string; variant: 'default' | 'secondary' | 'success' | 'warning' | 'destructive' | 'muted' }> = {
  ready: { label: 'Ready', variant: 'success' },
  completed: { label: 'Completed', variant: 'success' },
  connected: { label: 'Connected', variant: 'success' },
  published: { label: 'Published', variant: 'success' },
  active: { label: 'Active', variant: 'success' },
  draft: { label: 'Draft', variant: 'muted' },
  pending: { label: 'Pending', variant: 'warning' },
  queued: { label: 'Queued', variant: 'warning' },
  scheduled: { label: 'Scheduled', variant: 'warning' },
  processing: { label: 'Processing', variant: 'warning' },
  rendering: { label: 'Rendering', variant: 'warning' },
  publishing: { label: 'Publishing', variant: 'warning' },
  transcribing: { label: 'Transcribing', variant: 'warning' },
  analyzing: { label: 'Analysing', variant: 'warning' },
  failed: { label: 'Failed', variant: 'destructive' },
  cancelled: { label: 'Cancelled', variant: 'muted' },
  expired: { label: 'Expired', variant: 'destructive' },
  rejected: { label: 'Rejected', variant: 'destructive' },
  approved: { label: 'Approved', variant: 'success' },
  archived: { label: 'Archived', variant: 'muted' },
  superseded: { label: 'Superseded', variant: 'muted' },
};

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  const entry = MAP[status] ?? { label: status.replace(/_/g, ' '), variant: 'secondary' as const };
  return (
    <Badge variant={entry.variant} className={className}>
      {entry.label}
    </Badge>
  );
}
