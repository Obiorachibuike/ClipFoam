'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, ImagePlus, Palette, Pencil, Plus, Star, Trash2, Upload } from 'lucide-react';

import { ConfirmDialog } from '@/components/shared/confirm-dialog';
import { EmptyState } from '@/components/shared/empty-state';
import { ErrorState } from '@/components/shared/error-state';
import { PageHeader } from '@/components/shared/page-header';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { api, errorMessage } from '@/lib/api';
import { toast } from '@/lib/store';
import type { BrandKit, VisualPreset } from '@/lib/types';

const PRESETS: { id: VisualPreset; label: string }[] = [
  { id: 'bold_creator', label: 'Bold creator' },
  { id: 'karaoke', label: 'Karaoke' },
  { id: 'minimal', label: 'Minimal' },
  { id: 'boxed', label: 'Boxed' },
  { id: 'podcast', label: 'Podcast' },
  { id: 'neon', label: 'Neon' },
  { id: 'classic', label: 'Classic' },
  { id: 'editorial', label: 'Editorial' },
];

type Draft = {
  name: string;
  primary_color: string;
  secondary_color: string;
  text_color: string;
  highlight_color: string;
  font_family: string;
  heading_font: string;
  caption_preset: VisualPreset;
  intro_text: string;
  outro_text: string;
  is_default: boolean;
};

const BLANK: Draft = {
  name: '',
  primary_color: '#39FF14',
  secondary_color: '#101619',
  text_color: '#F5F7F6',
  highlight_color: '#39FF14',
  font_family: 'Inter',
  heading_font: 'Inter',
  caption_preset: 'bold_creator',
  intro_text: '',
  outro_text: '',
  is_default: false,
};

export default function BrandKitsPage() {
  const [kits, setKits] = useState<BrandKit[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(BLANK);
  const [saving, setSaving] = useState(false);
  const logoRef = useRef<HTMLInputElement | null>(null);
  const [logoKitId, setLogoKitId] = useState<string | null>(null);
  const [uploadingLogo, setUploadingLogo] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await api.get<BrandKit[]>('/brand-kits');
      setKits(data);
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

  function openCreate() {
    setEditingId(null);
    setDraft(BLANK);
    setDialogOpen(true);
  }

  function openEdit(kit: BrandKit) {
    setEditingId(kit.id);
    setDraft({
      name: kit.name,
      primary_color: kit.primary_color,
      secondary_color: kit.secondary_color,
      text_color: kit.text_color,
      highlight_color: kit.highlight_color,
      font_family: kit.font_family,
      heading_font: kit.heading_font,
      caption_preset: kit.caption_preset,
      intro_text: kit.intro_text ?? '',
      outro_text: kit.outro_text ?? '',
      is_default: kit.is_default,
    });
    setDialogOpen(true);
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    try {
      const payload = {
        ...draft,
        intro_text: draft.intro_text || null,
        outro_text: draft.outro_text || null,
      };
      if (editingId) {
        await api.patch<BrandKit>(`/brand-kits/${editingId}`, payload);
        toast({ title: 'Brand kit updated', variant: 'success' });
      } else {
        await api.post<BrandKit>('/brand-kits', payload);
        toast({ title: 'Brand kit created', variant: 'success' });
      }
      setDialogOpen(false);
      await load();
    } catch (err) {
      toast({ title: 'Could not save brand kit', description: errorMessage(err), variant: 'error' });
    } finally {
      setSaving(false);
    }
  }

  async function remove(kit: BrandKit) {
    try {
      await api.del(`/brand-kits/${kit.id}`);
      toast({ title: 'Brand kit deleted' });
      await load();
    } catch (err) {
      toast({ title: 'Delete failed', description: errorMessage(err), variant: 'error' });
    }
  }

  async function makeDefault(kit: BrandKit) {
    try {
      await api.patch(`/brand-kits/${kit.id}`, { is_default: true });
      await load();
      toast({ title: `${kit.name} is now the default kit`, variant: 'success' });
    } catch (err) {
      toast({ title: 'Update failed', description: errorMessage(err), variant: 'error' });
    }
  }

  async function uploadLogo(kitId: string, file: File) {
    setUploadingLogo(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      await api.upload<{ logo_url: string | null }>(`/brand-kits/${kitId}/logo`, formData);
      toast({ title: 'Logo uploaded', description: 'Used for the logo overlay in renders.', variant: 'success' });
      await load();
    } catch (err) {
      toast({ title: 'Logo upload failed', description: errorMessage(err), variant: 'error' });
    } finally {
      setUploadingLogo(false);
      setLogoKitId(null);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Identity"
        title="Brand kits"
        description="Colors, fonts and caption presets applied consistently across every clip and render."
        actions={
          <Button onClick={openCreate}>
            <Plus className="h-4 w-4" /> New brand kit
          </Button>
        }
      />

      {error ? <ErrorState message={error} onRetry={() => void load()} /> : null}

      {loading ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 3 }).map((_, index) => (
            <Skeleton key={index} className="h-56 w-full" />
          ))}
        </div>
      ) : kits.length ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {kits.map((kit) => (
            <Card key={kit.id} className={kit.is_default ? 'border-primary/50' : undefined}>
              <CardHeader className="gap-2 pb-3">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <CardTitle className="text-base">{kit.name}</CardTitle>
                    <CardDescription className="mt-1">
                      {PRESETS.find((preset) => preset.id === kit.caption_preset)?.label ?? kit.caption_preset}
                    </CardDescription>
                  </div>
                  {kit.is_default ? (
                    <Badge className="shrink-0">
                      <Star className="mr-1 h-3 w-3" /> default
                    </Badge>
                  ) : null}
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="flex h-14 overflow-hidden rounded-lg border border-border">
                  {[kit.primary_color, kit.secondary_color, kit.text_color, kit.highlight_color].map((color, index) => (
                    <div key={`${color}-${index}`} className="flex-1" style={{ backgroundColor: color }} />
                  ))}
                </div>
                <div className="flex items-center gap-2">
                  {kit.logo_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={kit.logo_url} alt={`${kit.name} logo`} className="h-8 w-8 rounded border border-border object-contain" />
                  ) : (
                    <span className="flex h-8 w-8 items-center justify-center rounded border border-dashed border-border text-muted-foreground">
                      <ImagePlus className="h-3.5 w-3.5" />
                    </span>
                  )}
                  <span className="text-xs text-muted-foreground">
                    {kit.font_family} · {kit.heading_font} headings
                  </span>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  <Button size="sm" variant="secondary" onClick={() => openEdit(kit)}>
                    <Pencil className="h-3.5 w-3.5" /> Edit
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      setLogoKitId(kit.id);
                      logoRef.current?.click();
                    }}
                    loading={uploadingLogo && logoKitId === kit.id}
                  >
                    <Upload className="h-3.5 w-3.5" /> Logo
                  </Button>
                  {!kit.is_default ? (
                    <Button size="sm" variant="ghost" onClick={() => void makeDefault(kit)}>
                      <Check className="h-3.5 w-3.5" /> Default
                    </Button>
                  ) : null}
                  <ConfirmDialog
                    trigger={
                      <Button size="sm" variant="ghost" className="text-red-400" aria-label="Delete brand kit">
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    }
                    title={`Delete “${kit.name}”?`}
                    description="Clips already rendered keep their output; new renders will fall back to the default style."
                    confirmLabel="Delete kit"
                    destructive
                    onConfirm={() => remove(kit)}
                  />
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <EmptyState
          icon={Palette}
          title="No brand kits yet"
          description="Create a kit to keep captions, colors and logos consistent across every clip."
          action={
            <Button size="sm" onClick={openCreate}>
              <Plus className="h-3.5 w-3.5" /> New brand kit
            </Button>
          }
        />
      )}

      <input
        ref={logoRef}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        className="sr-only"
        aria-label="Upload brand logo"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file && logoKitId) void uploadLogo(logoKitId, file);
          event.target.value = '';
        }}
      />

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-h-[90vh] max-w-xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingId ? 'Edit brand kit' : 'New brand kit'}</DialogTitle>
            <DialogDescription>These values feed both the editor preview and the FFmpeg render.</DialogDescription>
          </DialogHeader>
          <form onSubmit={save} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="kit-name">Name</Label>
              <Input
                id="kit-name"
                required
                value={draft.name}
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                placeholder="Night Shift Studio"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              {([
                ['primary_color', 'Primary'],
                ['secondary_color', 'Background'],
                ['text_color', 'Text'],
                ['highlight_color', 'Highlight'],
              ] as const).map(([key, label]) => (
                <div key={key} className="space-y-1.5">
                  <Label htmlFor={key}>{label}</Label>
                  <div className="flex gap-2">
                    <Input
                      id={key}
                      type="color"
                      className="h-9 w-12 p-1"
                      value={draft[key]}
                      onChange={(event) => setDraft({ ...draft, [key]: event.target.value.toUpperCase() })}
                    />
                    <Input
                      value={draft[key]}
                      onChange={(event) => setDraft({ ...draft, [key]: event.target.value.toUpperCase() })}
                      className="font-mono text-xs"
                      maxLength={7}
                    />
                  </div>
                </div>
              ))}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label>Caption preset</Label>
                <Select
                  value={draft.caption_preset}
                  onValueChange={(value) => setDraft({ ...draft, caption_preset: value as VisualPreset })}
                >
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {PRESETS.map((preset) => (
                      <SelectItem key={preset.id} value={preset.id}>{preset.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="font">Font family</Label>
                <Input
                  id="font"
                  value={draft.font_family}
                  onChange={(event) => setDraft({ ...draft, font_family: event.target.value })}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="intro">Intro text</Label>
                <Input
                  id="intro"
                  value={draft.intro_text}
                  maxLength={200}
                  onChange={(event) => setDraft({ ...draft, intro_text: event.target.value })}
                  placeholder="New episode"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="outro">Outro text</Label>
                <Input
                  id="outro"
                  value={draft.outro_text}
                  maxLength={200}
                  onChange={(event) => setDraft({ ...draft, outro_text: event.target.value })}
                  placeholder="Follow for more"
                />
              </div>
            </div>
            <label className="flex items-center justify-between rounded-lg border border-border bg-secondary/30 px-3 py-2.5 text-sm">
              <span>Use as workspace default</span>
              <Switch
                checked={draft.is_default}
                onCheckedChange={(checked) => setDraft({ ...draft, is_default: checked })}
              />
            </label>
            <DialogFooter>
              <Button type="submit" loading={saving}>{editingId ? 'Save changes' : 'Create kit'}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
