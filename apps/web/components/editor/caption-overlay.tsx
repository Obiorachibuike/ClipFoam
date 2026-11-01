'use client';

import type { CaptionLine, CaptionStyle } from '@/lib/types';

/**
 * Browser preview of the burned-in captions.
 *
 * The authoritative render happens in FFmpeg with libass; this overlay mirrors
 * the same style model (preset, position, uppercase, karaoke highlight) so the
 * editor preview is honest about timing and text rather than a generic mockup.
 * Only the currently active line is shown, exactly like the rendered output.
 */
export function CaptionOverlay({
  style,
  lines,
  time,
  className,
}: {
  style: CaptionStyle;
  lines: CaptionLine[];
  time: number;
  className?: string;
}) {
  const active = lines.find((line) => time >= line.start - 0.05 && time <= line.end + 0.15);
  const position =
    style.position === 'top' ? 'items-start' : style.position === 'center' ? 'items-center' : 'items-end';

  const fontScale = (style.font_size ?? 62) / 1920;
  const margin = style.safe_area_margin ?? 8;

  return (
    <div
      className={`pointer-events-none absolute inset-0 flex justify-center ${position} ${className ?? ''}`}
      style={{
        containerType: 'size',
        paddingLeft: `${margin}%`,
        paddingRight: `${margin}%`,
        paddingTop: `${Math.max(6, margin)}%`,
        paddingBottom: `${Math.max(8, margin + 4)}%`,
      }}
      aria-hidden
    >
      {active ? (
        <div
          key={`${active.start}-${active.text}`}
          className="w-full text-center"
          style={{
            fontSize: `${fontScale * 100}cqh`,
            lineHeight: 1.08,
            fontFamily: style.font_family,
            fontWeight: 800,
            letterSpacing: '0.01em',
            color: style.text_color,
            textTransform: style.uppercase ? 'uppercase' : 'none',
            textAlign: style.alignment === 'left' ? 'left' : style.alignment === 'right' ? 'right' : 'center',
            backgroundColor: `rgba(0,0,0,${Math.min(1, style.background_opacity ?? 0)})`,
            boxShadow:
              (style.background_opacity ?? 0) > 0.02
                ? `0 0 0 ${0.6 * fontScale * 400}px rgba(0,0,0,${style.background_opacity})`
                : undefined,
            WebkitTextStroke: `${Math.max(0.4, (style.outline_width ?? 3) / 4)}px ${style.outline_color}`,
            paintOrder: 'stroke fill',
            textShadow: `0 ${(style.shadow ?? 1) / 3}px ${(style.shadow ?? 1)}px rgba(0,0,0,0.55)`,
            animation: style.animation === 'none' ? undefined : 'caption-pop 220ms ease-out',
            wordBreak: 'break-word',
          }}
        >
          {active.words.length
            ? active.words.map((word, index) => {
                const spoken = word.end <= time + 0.02;
                return (
                  <span
                    key={`${word.text}-${index}`}
                    style={{
                      color:
                        (style.animation === 'karaoke' || style.animation === 'pop') && spoken
                          ? style.highlight_color
                          : undefined,
                      transition: 'color 90ms linear',
                    }}
                  >
                    {word.text}
                    {index < active.words.length - 1 ? ' ' : ''}
                  </span>
                );
              })
            : active.text}
        </div>
      ) : null}
    </div>
  );
}
