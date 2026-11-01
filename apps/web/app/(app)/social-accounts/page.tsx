'use client';

import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import {
  AlertTriangle,
  CheckCircle2,
  CircleSlash,
  Link2,
  Loader2,
  ShieldCheck,
  Unplug,
} from 'lucide-react';

import { ConfirmDialog } from '@/components/shared/confirm-dialog';
import { EmptyState } from '@/components/shared/empty-state';
import { ErrorState } from '@/components/shared/error-state';
import { PageHeader } from '@/components/shared/page-header';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { api, errorMessage } from '@/lib/api';
import { formatDate } from '@/lib/format';
import { toast } from '@/lib/store';
import type { RuntimeConfig, SocialAccount, SocialProvider } from '@/lib/types';

const STATUS_META: Record<
  SocialProvider['status'],
  { label: string; variant: 'default' | 'secondary' | 'muted' | 'warning'; icon: typeof CheckCircle2 }
> = {
  available: { label: 'Ready', variant: 'default', icon: CheckCircle2 },
  needs_configuration: { label: 'Needs credentials', variant: 'warning', icon: AlertTriangle },
  unavailable: { label: 'Unavailable', variant: 'muted', icon: CircleSlash },
  manual_export: { label: 'Manual export', variant: 'secondary', icon: Unplug },
};

export default function SocialAccountsPage() {
  const params = useSearchParams();
  const [providers, setProviders] = useState<SocialProvider[]>([]);
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [config, setConfig] = useState<RuntimeConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [providerList, accountList, runtime] = await Promise.all([
        api.get<SocialProvider[]>('/social/providers'),
        api.get<SocialAccount[]>('/social/accounts'),
        api.get<RuntimeConfig>('/system/config').catch(() => null),
      ]);
      setProviders(providerList);
      setAccounts(accountList);
      setConfig(runtime);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const connected = params.get('connected');
    const failed = params.get('error');
    if (connected) toast({ title: `Connected ${connected}`, variant: 'success' });
    if (failed) {
      toast({
        title: 'Connection failed',
        description: `The provider returned “${failed}”. Check credentials, scopes and the redirect URI.`,
        variant: 'error',
      });
    }
  }, [params]);

  async function connect(platform: string) {
    setBusy(platform);
    try {
      const { authorization_url } = await api.post<{ authorization_url: string }>(
        `/social/connect/${platform}`,
        {},
      );
      window.location.href = authorization_url;
    } catch (err) {
      toast({ title: 'Could not start OAuth', description: errorMessage(err), variant: 'error' });
      setBusy(null);
    }
  }

  async function disconnect(account: SocialAccount) {
    setBusy(account.id);
    try {
      await api.del(`/social/accounts/${account.id}`);
      toast({ title: `${account.display_name} disconnected` });
      await load();
    } catch (err) {
      toast({ title: 'Disconnect failed', description: errorMessage(err), variant: 'error' });
    } finally {
      setBusy(null);
    }
  }

  const accountFor = (platform: string) => accounts.find((account) => account.platform === platform);

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Connections"
        title="Social accounts"
        description="OAuth tokens are encrypted at rest and every connection stores the scopes it was granted."
        actions={<Badge variant="muted" className="h-7 px-3">Publishing {config?.social_publish_enabled ? 'enabled' : 'disabled'}</Badge>}
      />

      <div className="flex items-start gap-2 rounded-lg border border-border bg-secondary/30 px-4 py-3 text-xs leading-relaxed text-muted-foreground">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div>
          <p className="font-medium text-foreground">Honest integration status</p>
          <p className="mt-0.5">
            Only platforms marked “Ready” have a real upload path implemented. Everything else is
            exported manually — ClipFoam never reports a post as published unless the provider API
            returned an id. Redirect URI:{' '}
            <span className="font-mono">/api/v1/social/callback/&lt;platform&gt;</span>.
          </p>
        </div>
      </div>

      {error ? <ErrorState message={error} onRetry={() => void load()} /> : null}

      {loading ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, index) => (
            <Skeleton key={index} className="h-56 w-full" />
          ))}
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {providers.map((provider) => {
            const meta = STATUS_META[provider.status] ?? STATUS_META.unavailable;
            const account = accountFor(provider.id);
            return (
              <Card key={provider.id} className={account ? 'border-primary/40' : undefined}>
                <CardHeader className="gap-2 pb-3">
                  <div className="flex items-start justify-between gap-2">
                    <CardTitle className="text-base">{provider.name}</CardTitle>
                    <Badge variant={meta.variant}>
                      <meta.icon className="mr-1 h-3 w-3" /> {meta.label}
                    </Badge>
                  </div>
                  <CardDescription className="text-[11px] leading-relaxed">{provider.notes}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  {account ? (
                    <div className="rounded-lg border border-primary/30 bg-primary/[0.06] px-3 py-2">
                      <p className="truncate text-sm font-medium">{account.display_name}</p>
                      <p className="mt-0.5 text-[11px] text-muted-foreground">
                        Connected {formatDate(account.created_at)} · {account.status}
                        {account.token_expires_at ? ` · token expires ${formatDate(account.token_expires_at)}` : ''}
                      </p>
                      {account.scopes.length ? (
                        <p className="mt-1 truncate font-mono text-[10px] text-muted-foreground">
                          scopes: {account.scopes.map(String).join(' ')}
                        </p>
                      ) : null}
                    </div>
                  ) : null}

                  <dl className="space-y-1.5 text-[11px] text-muted-foreground">
                    <div className="flex gap-2">
                      <dt className="w-24 shrink-0">Formats</dt>
                      <dd className="truncate">{provider.supported_formats.join(', ') || '—'}</dd>
                    </div>
                    <div className="flex gap-2">
                      <dt className="w-24 shrink-0">Limits</dt>
                      <dd className="line-clamp-2">{provider.upload_restrictions.join(' · ') || '—'}</dd>
                    </div>
                    <div className="flex gap-2">
                      <dt className="w-24 shrink-0">Status check</dt>
                      <dd>{provider.supports_status_check ? 'supported' : 'not available'}</dd>
                    </div>
                  </dl>

                  {provider.publishing_limitations.length ? (
                    <p className="rounded bg-secondary/50 px-2 py-1.5 text-[10px] leading-relaxed text-muted-foreground">
                      {provider.publishing_limitations[0]}
                    </p>
                  ) : null}

                  <div className="flex flex-wrap gap-2">
                    {account ? (
                      <ConfirmDialog
                        trigger={
                          <Button size="sm" variant="outline" loading={busy === account.id}>
                            <Unplug className="h-3.5 w-3.5" /> Disconnect
                          </Button>
                        }
                        title={`Disconnect ${account.display_name}?`}
                        description="Stored tokens are deleted. Scheduled posts for this platform will fail until you reconnect."
                        confirmLabel="Disconnect"
                        destructive
                        onConfirm={() => disconnect(account)}
                      />
                    ) : provider.status === 'available' && provider.supports_oauth ? (
                      <Button size="sm" onClick={() => void connect(provider.id)} loading={busy === provider.id}>
                        <Link2 className="h-3.5 w-3.5" /> Connect
                      </Button>
                    ) : provider.status === 'needs_configuration' ? (
                      <Button size="sm" variant="secondary" disabled>
                        <AlertTriangle className="h-3.5 w-3.5" /> Missing credentials
                      </Button>
                    ) : (
                      <Button size="sm" variant="ghost" disabled>
                        <Unplug className="h-3.5 w-3.5" /> Manual export only
                      </Button>
                    )}
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      {!loading && !providers.length && !error ? (
        <EmptyState
          icon={Loader2}
          title="No providers reported"
          description="The backend returned an empty provider registry, which should not happen. Check the API logs."
        />
      ) : null}
    </div>
  );
}
