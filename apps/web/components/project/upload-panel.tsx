'use client';

import { useRef, useState } from 'react';
import { FileVideo, Link2, Loader2, UploadCloud } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { api, errorMessage } from '@/lib/api';
import { formatBytes } from '@/lib/format';
import { toast } from '@/lib/store';
import type { MediaAsset, UploadResponse } from '@/lib/types';
import { cn } from '@/lib/utils';

export function UploadPanel({
  projectId,
  disabled,
  onStarted,
}: {
  projectId: string;
  disabled?: boolean;
  onStarted: (jobId: string) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [progressLabel, setProgressLabel] = useState<string | null>(null);
  const [importUrl, setImportUrl] = useState('');
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function uploadFile(file: File) {
    setError(null);
    setUploading(true);
    setProgressLabel(`Uploading ${file.name} (${formatBytes(file.size)})…`);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const result = await api.upload<UploadResponse>(`/media/projects/${projectId}/upload`, formData);
      toast({ title: 'Upload accepted', description: 'Validating and probing metadata…', variant: 'success' });
      onStarted(result.job.id);
    } catch (err) {
      const message = errorMessage(err);
      setError(message);
      toast({ title: 'Upload failed', description: message, variant: 'error' });
    } finally {
      setUploading(false);
      setProgressLabel(null);
      if (inputRef.current) inputRef.current.value = '';
    }
  }

  async function submitImport(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setImporting(true);
    try {
      const media = await api.post<MediaAsset>(`/media/projects/${projectId}/import`, { url: importUrl.trim() });
      toast({ title: 'Import queued', description: 'Downloading and validating the remote file.', variant: 'success' });
      setImportUrl('');
      onStarted('');
      void media;
    } catch (err) {
      const message = errorMessage(err);
      setError(message);
      toast({ title: 'Import rejected', description: message, variant: 'error' });
    } finally {
      setImporting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Source media</CardTitle>
        <CardDescription>Upload a file or import an authorized direct URL. Files are validated, then stored privately.</CardDescription>
      </CardHeader>
      <CardContent>
        <Tabs defaultValue="upload">
          <TabsList className="w-full justify-start">
            <TabsTrigger value="upload">
              <UploadCloud className="h-3.5 w-3.5" /> Upload
            </TabsTrigger>
            <TabsTrigger value="import">
              <Link2 className="h-3.5 w-3.5" /> URL import
            </TabsTrigger>
          </TabsList>

          <TabsContent value="upload" className="space-y-3">
            <div
              onDragOver={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                const file = event.dataTransfer.files?.[0];
                if (file) void uploadFile(file);
              }}
              className={cn(
                'flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border px-6 py-10 text-center transition-colors',
                dragging && 'border-primary bg-primary/5',
                (uploading || disabled) && 'opacity-70',
              )}
            >
              {uploading ? (
                <Loader2 className="h-6 w-6 animate-spin text-primary" aria-hidden />
              ) : (
                <FileVideo className="h-6 w-6 text-muted-foreground" aria-hidden />
              )}
              <p className="text-sm font-medium">{uploading ? 'Uploading…' : 'Drop a video here'}</p>
              <p className="max-w-sm text-xs text-muted-foreground">
                MP4, MOV, MKV, WebM, M4V, AVI or audio files. The server enforces size and duration limits.
              </p>
              <input
                ref={inputRef}
                type="file"
                accept="video/*,audio/*"
                className="sr-only"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void uploadFile(file);
                }}
                aria-label="Choose a video file"
              />
              <Button
                type="button"
                variant="secondary"
                size="sm"
                disabled={uploading || disabled}
                onClick={() => inputRef.current?.click()}
              >
                Choose file
              </Button>
              {progressLabel ? <p className="text-xs text-muted-foreground">{progressLabel}</p> : null}
            </div>
          </TabsContent>

          <TabsContent value="import">
            <form onSubmit={submitImport} className="space-y-3">
              <div className="space-y-1.5">
                <Label htmlFor="import-url">Direct media URL</Label>
                <Input
                  id="import-url"
                  type="url"
                  required
                  value={importUrl}
                  onChange={(event) => setImportUrl(event.target.value)}
                  placeholder="https://cdn.example.com/episode.mp4"
                />
              </div>
              <p className="text-xs leading-relaxed text-muted-foreground">
                Only direct file URLs from public hosts are accepted. Platform page links (YouTube,
                TikTok, etc.) are not downloadable — import a file you own or export one first.
                Private networks, redirects to internal hosts and oversized downloads are blocked.
              </p>
              <Button type="submit" size="sm" loading={importing} disabled={disabled}>
                Queue import
              </Button>
            </form>
          </TabsContent>
        </Tabs>
        {error ? <p className="mt-3 rounded-md bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</p> : null}
      </CardContent>
    </Card>
  );
}
