'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { ArrowLeft, FolderPlus } from 'lucide-react';
import Link from 'next/link';

import { ErrorState } from '@/components/shared/error-state';
import { PageHeader } from '@/components/shared/page-header';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { api, errorMessage } from '@/lib/api';
import { aspectLabel } from '@/lib/format';
import { toast } from '@/lib/store';
import type { AspectRatio, Project } from '@/lib/types';

const PLATFORMS = ['youtube', 'tiktok', 'instagram', 'x', 'linkedin'];

export default function NewProjectPage() {
  const router = useRouter();
  const [form, setForm] = useState({
    title: '',
    description: '',
    default_aspect_ratio: '9:16' as AspectRatio,
    default_platform: 'tiktok',
    source_language: '',
  });
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const project = await api.post<Project>('/projects', {
        title: form.title.trim(),
        description: form.description.trim() || null,
        default_aspect_ratio: form.default_aspect_ratio,
        default_platform: form.default_platform,
        source_language: form.source_language.trim() || null,
      });
      toast({ title: 'Project created', description: 'Upload your source video to get started.', variant: 'success' });
      router.push(`/projects/${project.id}`);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <PageHeader
        eyebrow="New project"
        title="Set up a project"
        description="Projects group source footage, transcripts, clips, exports and posts for one video."
        actions={
          <Button asChild variant="ghost" size="sm">
            <Link href="/projects">
              <ArrowLeft className="h-3.5 w-3.5" /> Back
            </Link>
          </Button>
        }
      />

      {error ? <ErrorState title="Could not create project" message={error} /> : null}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <FolderPlus className="h-4 w-4 text-primary" aria-hidden /> Project details
          </CardTitle>
          <CardDescription>Defaults can be changed later per clip.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-5">
            <div className="space-y-1.5">
              <Label htmlFor="title">Title</Label>
              <Input
                id="title"
                required
                maxLength={200}
                value={form.title}
                onChange={(event) => setForm({ ...form, title: event.target.value })}
                placeholder="Episode 42 — How to price a product"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="description">Description <span className="text-muted-foreground">(optional)</span></Label>
              <Textarea
                id="description"
                value={form.description}
                onChange={(event) => setForm({ ...form, description: event.target.value })}
                placeholder="What is this source video about?"
                maxLength={4000}
              />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label>Default aspect ratio</Label>
                <Select
                  value={form.default_aspect_ratio}
                  onValueChange={(value) => setForm({ ...form, default_aspect_ratio: value as AspectRatio })}
                >
                  <SelectTrigger aria-label="Default aspect ratio">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {(['9:16', '4:5', '1:1', '16:9'] as AspectRatio[]).map((ratio) => (
                      <SelectItem key={ratio} value={ratio}>
                        {aspectLabel(ratio)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Target platform</Label>
                <Select
                  value={form.default_platform}
                  onValueChange={(value) => setForm({ ...form, default_platform: value })}
                >
                  <SelectTrigger aria-label="Target platform">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {PLATFORMS.map((platform) => (
                      <SelectItem key={platform} value={platform}>
                        {platform}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="language">Source language <span className="text-muted-foreground">(optional)</span></Label>
              <Input
                id="language"
                value={form.source_language}
                onChange={(event) => setForm({ ...form, source_language: event.target.value })}
                placeholder="en"
                maxLength={16}
              />
              <p className="text-xs text-muted-foreground">
                Leaving this blank lets faster-whisper auto-detect the spoken language.
              </p>
            </div>
            <Button type="submit" loading={loading} className="w-full sm:w-auto">
              Create project
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
