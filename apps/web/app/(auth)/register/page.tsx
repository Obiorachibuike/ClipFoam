'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { ErrorState } from '@/components/shared/error-state';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { errorMessage } from '@/lib/api';
import { useSession } from '@/lib/store';

export default function RegisterPage() {
  const router = useRouter();
  const register = useSession((state) => state.register);
  const [form, setForm] = useState({ name: '', email: '', password: '', organization_name: '' });
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await register({
        name: form.name.trim(),
        email: form.email.trim(),
        password: form.password,
        organization_name: form.organization_name.trim() || undefined,
      });
      router.replace('/dashboard');
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="space-y-1.5">
        <h1 className="text-2xl font-semibold tracking-tight">Create your workspace</h1>
        <p className="text-sm text-muted-foreground">Start clipping in under a minute.</p>
      </div>
      {error ? <ErrorState title="Could not create account" message={error} /> : null}
      <form onSubmit={onSubmit} className="space-y-4" noValidate>
        <div className="space-y-1.5">
          <Label htmlFor="name">Your name</Label>
          <Input
            id="name"
            required
            minLength={1}
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
            placeholder="Ada Lovelace"
            autoComplete="name"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="organization_name">Workspace name <span className="text-muted-foreground">(optional)</span></Label>
          <Input
            id="organization_name"
            value={form.organization_name}
            onChange={(event) => setForm({ ...form, organization_name: event.target.value })}
            placeholder="Night Shift Studio"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
            required
            value={form.email}
            onChange={(event) => setForm({ ...form, email: event.target.value })}
            placeholder="you@studio.com"
            autoComplete="email"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            type="password"
            required
            minLength={8}
            value={form.password}
            onChange={(event) => setForm({ ...form, password: event.target.value })}
            placeholder="At least 8 characters"
            autoComplete="new-password"
          />
          <p className="text-xs text-muted-foreground">Mix letters with numbers or symbols.</p>
        </div>
        <Button type="submit" className="w-full" loading={loading}>
          Create workspace
        </Button>
      </form>
      <p className="text-center text-sm text-muted-foreground">
        Already have an account?{' '}
        <Link href="/login" className="font-medium text-primary hover:underline">
          Sign in
        </Link>
      </p>
    </div>
  );
}
