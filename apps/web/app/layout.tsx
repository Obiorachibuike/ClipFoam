import type { Metadata, Viewport } from 'next';

import { Toaster } from '@/components/shared/toaster';
import { TooltipProvider } from '@/components/ui/tooltip';

import './globals.css';

export const metadata: Metadata = {
  title: {
    default: 'ClipFoam — Turn long videos into short-form gold',
    template: '%s · ClipFoam',
  },
  description:
    'ClipFoam is an open-source AI video repurposing studio: transcribe long videos, find highlights, reframe and caption clips, render with FFmpeg, and publish to social platforms.',
  applicationName: 'ClipFoam',
  keywords: ['video repurposing', 'AI clipping', 'shorts', 'captions', 'FFmpeg', 'open source'],
  icons: {
    icon: [{ url: '/favicon.svg', type: 'image/svg+xml' }],
  },
};

export const viewport: Viewport = {
  themeColor: [
    { media: '(prefers-color-scheme: dark)', color: '#080B0D' },
    { media: '(prefers-color-scheme: light)', color: '#F5F7F6' },
  ],
};

const themeScript = `
try {
  var stored = localStorage.getItem('clipfoam-theme');
  var dark = stored ? stored === 'dark' : true;
  document.documentElement.classList.toggle('dark', dark);
  document.documentElement.style.colorScheme = dark ? 'dark' : 'light';
} catch (e) {}
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-dvh bg-background font-sans antialiased">
        <TooltipProvider delayDuration={200}>{children}</TooltipProvider>
        <Toaster />
      </body>
    </html>
  );
}
