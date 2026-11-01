'use client';

import { useEffect, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Database,
  KeyRound,
  Server,
  ShieldCheck,
  Trash2,
  UserCog,
  Users,
} from 'lucide-react';

import { ConfirmDialog } from '@/components/shared/confirm-dialog';
import { EmptyState } from '@/components/shared/empty-state';
import { ErrorState } from '@/components/shared/error-state';
import { PageHeader } from '@/components/shared/page-header';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { api, errorMessage } from '@/lib/api';
import { formatDate, timeAgo } from '@/lib/format';
import { useAsync } from '@/lib/hooks';
import { toast, useSession } from '@/lib/store';
import type { AuditLogEntry, Page, RuntimeConfig } from '@/lib/types';

export default function SettingsPage() {
  const { session, load } = useSession();
  const admin = session?.role === 'owner' || session?.role === 'admin';

  const runtime = useAsync<RuntimeConfig>(() => api.get('/system/config'), []);
  const audit = useAsync<Page<AuditLogEntry> | null>(
    () => (admin ? api.get<Page<AuditLogEntry>>('/system/audit-logs?limit=50') : Promise.resolve(null)),
    [admin],
  );

  const [name, setName] = useState('');
  const [orgName, setOrgName] = useState('');
  const [passwords, setPasswords] = useState({ current_password: '', new_password: '' });
  const [savingProfile, setSavingProfile] = useState(false);
  const [savingOrg, setSavingOrg] = useState(false);
  const [savingPassword, setSavingPassword] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<Record<string, unknown> | null>(null);
  const [confirmText, setConfirmText] = useState('');
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    if (session) {
      setName(session.user.name);
      setOrgName(session.organization.name);
    }
  }, [session]);

  async function saveProfile(event: React.FormEvent) {
    event.preventDefault();
    setSavingProfile(true);
    try {
      await api.patch('/auth/profile', { name });
      await load();
      toast({ title: 'Profile updated', variant: 'success' });
    } catch (err) {
      toast({ title: 'Update failed', description: errorMessage(err), variant: 'error' });
    } finally {
      setSavingProfile(false);
    }
  }

  async function saveOrganization(event: React.FormEvent) {
    event.preventDefault();
    setSavingOrg(true);
    try {
      await api.patch('/auth/organization', { name: orgName });
      await load();
      toast({ title: 'Workspace renamed', variant: 'success' });
    } catch (err) {
      toast({ title: 'Rename failed', description: errorMessage(err), variant: 'error' });
    } finally {
      setSavingOrg(false);
    }
  }

  async function changePassword(event: React.FormEvent) {
    event.preventDefault();
    setSavingPassword(true);
    try {
      await api.post('/auth/password', passwords);
      setPasswords({ current_password: '', new_password: '' });
      toast({
        title: 'Password changed',
        description: 'Other devices are signed out because tokens are bound to the password hash.',
        variant: 'success',
      });
    } catch (err) {
      toast({ title: 'Password change failed', description: errorMessage(err), variant: 'error' });
    } finally {
      setSavingPassword(false);
    }
  }

  async function testProvider() {
    setTesting(true);
    try {
      const result = await api.post<Record<string, unknown>>('/system/providers/test', { provider: null });
      setTestResult(result);
      toast({
        title: result.ok ? 'Provider responded' : 'Provider check failed',
        description: String(result.detail ?? result.message ?? ''),
        variant: result.ok ? 'success' : 'error',
      });
    } catch (err) {
      toast({ title: 'Provider test failed', description: errorMessage(err), variant: 'error' });
    } finally {
      setTesting(false);
    }
  }

  async function deleteWorkspace() {
    if (confirmText !== 'DELETE') {
      toast({ title: 'Type DELETE to confirm', variant: 'error' });
      return;
    }
    setDeleting(true);
    try {
      await api.del('/account', { confirm: 'DELETE' });
      toast({
        title: 'Deletion scheduled',
        description: 'A background job is removing media and records. You will be signed out shortly.',
        variant: 'success',
      });
    } catch (err) {
      toast({ title: 'Deletion failed', description: errorMessage(err), variant: 'error' });
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Workspace"
        title="Settings"
        description="Account, workspace, provider and data controls. Destructive actions always require confirmation."
      />

      <Tabs defaultValue="account" className="space-y-4">
        <TabsList>
          <TabsTrigger value="account"><UserCog className="h-3.5 w-3.5" /> Account</TabsTrigger>
          <TabsTrigger value="workspace"><Users className="h-3.5 w-3.5" /> Workspace</TabsTrigger>
          <TabsTrigger value="providers"><Server className="h-3.5 w-3.5" /> Providers</TabsTrigger>
          {admin ? <TabsTrigger value="audit"><Activity className="h-3.5 w-3.5" /> Audit log</TabsTrigger> : null}
          <TabsTrigger value="danger"><AlertTriangle className="h-3.5 w-3.5" /> Danger</TabsTrigger>
        </TabsList>

        <TabsContent value="account" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Profile</CardTitle>
              <CardDescription>{session?.user.email}</CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={saveProfile} className="flex flex-col gap-3 sm:flex-row sm:items-end">
                <div className="flex-1 space-y-1.5">
                  <Label htmlFor="profile-name">Display name</Label>
                  <Input id="profile-name" value={name} onChange={(event) => setName(event.target.value)} required maxLength={120} />
                </div>
                <Button type="submit" loading={savingProfile}>Save profile</Button>
              </form>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <KeyRound className="h-4 w-4 text-primary" aria-hidden /> Password
              </CardTitle>
              <CardDescription>Changing your password invalidates sessions on other devices.</CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={changePassword} className="grid gap-3 sm:grid-cols-3 sm:items-end">
                <div className="space-y-1.5">
                  <Label htmlFor="current-password">Current password</Label>
                  <Input
                    id="current-password"
                    type="password"
                    autoComplete="current-password"
                    value={passwords.current_password}
                    onChange={(event) => setPasswords({ ...passwords, current_password: event.target.value })}
                    required
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="new-password">New password</Label>
                  <Input
                    id="new-password"
                    type="password"
                    autoComplete="new-password"
                    minLength={8}
                    value={passwords.new_password}
                    onChange={(event) => setPasswords({ ...passwords, new_password: event.target.value })}
                    required
                  />
                </div>
                <Button type="submit" loading={savingPassword}>Change password</Button>
              </form>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Your session</CardTitle>
              <CardDescription>Authentication is a signed HttpOnly cookie with CSRF protection.</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-3 text-xs sm:grid-cols-3">
              <div className="rounded-lg border border-border bg-secondary/40 px-3 py-2">
                <p className="mono-label">Role</p>
                <p className="mt-0.5 text-sm font-medium capitalize">{session?.role}</p>
              </div>
              <div className="rounded-lg border border-border bg-secondary/40 px-3 py-2">
                <p className="mono-label">Workspace</p>
                <p className="mt-0.5 truncate text-sm font-medium">{session?.organization.slug}</p>
              </div>
              <div className="rounded-lg border border-border bg-secondary/40 px-3 py-2">
                <p className="mono-label">Memberships</p>
                <p className="mt-0.5 text-sm font-medium">{session?.memberships.length}</p>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="workspace" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Workspace</CardTitle>
              <CardDescription>
                {admin ? 'Only admins can rename the workspace.' : 'Ask an owner or admin to rename the workspace.'}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={saveOrganization} className="flex flex-col gap-3 sm:flex-row sm:items-end">
                <div className="flex-1 space-y-1.5">
                  <Label htmlFor="org-name">Name</Label>
                  <Input
                    id="org-name"
                    value={orgName}
                    onChange={(event) => setOrgName(event.target.value)}
                    disabled={!admin}
                    maxLength={160}
                  />
                </div>
                <Button type="submit" loading={savingOrg} disabled={!admin}>Save workspace</Button>
              </form>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Plan & limits</CardTitle>
              <CardDescription>Limits are read from the server environment and enforced in the API.</CardDescription>
            </CardHeader>
            <CardContent>
              {runtime.loading ? (
                <Skeleton className="h-24 w-full" />
              ) : runtime.error ? (
                <ErrorState message={runtime.error} onRetry={runtime.reload} />
              ) : (
                <dl className="grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
                  {[
                    ['Plan', session?.organization.plan ?? '—'],
                    ['Max upload', `${runtime.data?.max_upload_mb ?? '—'} MB`],
                    ['Max duration', `${runtime.data?.max_duration_seconds ?? '—'} s`],
                    ['Retention', `${runtime.data?.retention_days ?? '—'} days`],
                  ].map(([label, value]) => (
                    <div key={label} className="rounded-lg border border-border bg-secondary/40 px-3 py-2">
                      <dt className="mono-label">{label}</dt>
                      <dd className="mt-0.5 text-sm font-medium">{value}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="providers" className="space-y-4">
          <Card>
            <CardHeader className="flex-row items-start justify-between gap-3">
              <div>
                <CardTitle>Runtime providers</CardTitle>
                <CardDescription>Live capability check against the current configuration.</CardDescription>
              </div>
              <Button size="sm" variant="outline" onClick={() => void testProvider()} loading={testing} disabled={!admin}>
                Test AI provider
              </Button>
            </CardHeader>
            <CardContent className="space-y-3">
              {runtime.loading ? (
                <Skeleton className="h-32 w-full" />
              ) : (
                <>
                  {[
                    { title: 'Transcription', data: runtime.data?.transcription },
                    { title: 'Language model', data: runtime.data?.ai },
                    { title: 'Storage', data: runtime.data?.storage },
                  ].map(({ title, data }) => (
                    <div key={title} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border px-3 py-2.5">
                      <div>
                        <p className="text-sm font-medium">{title}</p>
                        <p className="text-[11px] text-muted-foreground">{data?.label} · {data?.detail}</p>
                      </div>
                      <Badge variant={data?.configured ? 'default' : 'warning'}>
                        {data?.configured ? <CheckCircle2 className="mr-1 h-3 w-3" /> : <AlertTriangle className="mr-1 h-3 w-3" />}
                        {data?.configured ? 'configured' : 'not configured'}
                      </Badge>
                    </div>
                  ))}
                  <div className="flex flex-wrap gap-2 text-[11px] text-muted-foreground">
                    <Badge variant={runtime.data?.ffmpeg_available ? 'secondary' : 'warning'}>FFmpeg {runtime.data?.ffmpeg_available ? 'available' : 'missing'}</Badge>
                    <Badge variant={runtime.data?.ffprobe_available ? 'secondary' : 'muted'}>FFprobe {runtime.data?.ffprobe_available ? 'available' : 'fallback (PyAV)'}</Badge>
                    <Badge variant="muted">env: {runtime.data?.app_env}</Badge>
                    <Badge variant="muted">signup {runtime.data?.enable_signup ? 'open' : 'closed'}</Badge>
                  </div>
                  {testResult ? (
                    <pre className="max-h-40 overflow-auto rounded-lg border border-border bg-secondary/40 p-3 text-[11px]">
                      {JSON.stringify(testResult, null, 2)}
                    </pre>
                  ) : null}
                </>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {admin ? (
          <TabsContent value="audit" className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Activity className="h-4 w-4 text-primary" aria-hidden /> Audit log
                </CardTitle>
                <CardDescription>Security-relevant events recorded for this workspace.</CardDescription>
              </CardHeader>
              <CardContent>
                {audit.loading ? (
                  <Skeleton className="h-40 w-full" />
                ) : audit.error ? (
                  <ErrorState message={audit.error} onRetry={audit.reload} />
                ) : audit.data?.items.length ? (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>When</TableHead>
                        <TableHead>Action</TableHead>
                        <TableHead>Target</TableHead>
                        <TableHead>Metadata</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {audit.data.items.map((entry) => (
                        <TableRow key={entry.id}>
                          <TableCell className="whitespace-nowrap text-xs text-muted-foreground" title={formatDate(entry.created_at)}>
                            {timeAgo(entry.created_at)}
                          </TableCell>
                          <TableCell className="font-mono text-xs">{entry.action}</TableCell>
                          <TableCell className="text-xs text-muted-foreground">
                            {entry.target_type ? `${entry.target_type}${entry.target_id ? ` · ${entry.target_id.slice(0, 8)}` : ''}` : '—'}
                          </TableCell>
                          <TableCell className="max-w-[280px] truncate text-[11px] text-muted-foreground">
                            {Object.keys(entry.metadata_json ?? {}).length ? JSON.stringify(entry.metadata_json) : '—'}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                ) : (
                  <EmptyState icon={Database} title="No audit entries" description="Actions such as uploads, renders and publishing attempts will appear here." />
                )}
              </CardContent>
            </Card>
          </TabsContent>
        ) : null}

        <TabsContent value="danger" className="space-y-4">
          <Card className="border-red-500/30">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-red-300">
                <Trash2 className="h-4 w-4" aria-hidden /> Delete workspace
              </CardTitle>
              <CardDescription>
                Queues a durable deletion job for media, exports and records. This cannot be undone.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="space-y-1.5">
                <Label htmlFor="confirm-delete">Type DELETE to confirm</Label>
                <Input
                  id="confirm-delete"
                  value={confirmText}
                  onChange={(event) => setConfirmText(event.target.value)}
                  placeholder="DELETE"
                  disabled={!admin}
                  className="max-w-xs"
                />
              </div>
              <ConfirmDialog
                trigger={
                  <Button variant="destructive" disabled={!admin || confirmText !== 'DELETE'} loading={deleting}>
                    <Trash2 className="h-4 w-4" /> Delete workspace
                  </Button>
                }
                title="Delete this entire workspace?"
                description="Every project, clip, export and stored file will be removed by a background job. This action is irreversible."
                confirmLabel="Yes, delete everything"
                destructive
                onConfirm={deleteWorkspace}
              />
              {!admin ? (
                <p className="text-[11px] text-muted-foreground">Only workspace owners and admins can delete the workspace.</p>
              ) : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <ShieldCheck className="h-4 w-4 text-primary" aria-hidden /> Data handling
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-[11px] leading-relaxed text-muted-foreground">
              <p>· Media and exports are stored privately and served through HMAC-signed, expiring URLs.</p>
              <p>· OAuth tokens are encrypted with the server-side secret before storage.</p>
              <p>· Retention: {runtime.data?.retention_days ?? '—'} days, applied by the cleanup job.</p>
              <p>· Audit logs record actor, action, target and request metadata for security review.</p>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
