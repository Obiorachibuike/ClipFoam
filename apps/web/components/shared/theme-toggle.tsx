'use client';

import { Moon, Sun } from 'lucide-react';
import { useEffect } from 'react';

import { Button } from '@/components/ui/button';
import { useTheme } from '@/lib/store';

export function ThemeToggle() {
  const { theme, toggle, setTheme } = useTheme();

  useEffect(() => {
    const stored = window.localStorage.getItem('clipfoam-theme');
    if (stored === 'light' || stored === 'dark') {
      setTheme(stored);
    } else {
      setTheme('dark');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggle}
      aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
      title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
    >
      {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </Button>
  );
}
