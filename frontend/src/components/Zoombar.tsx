import React, { useCallback } from 'react';
import { motion } from 'framer-motion';
import { useReactFlow, useViewport } from 'reactflow';
import { ZoomIn, ZoomOut, Maximize2 } from 'lucide-react';

interface ZoomBarProps {
  /** Optional: called when user clicks the fit/reset button */
  onFitView?: () => void;
}

export function ZoomBar({ onFitView }: ZoomBarProps) {
  const { zoomIn, zoomOut, zoomTo, fitView } = useReactFlow();
  const { zoom } = useViewport();

  const MIN_ZOOM = 0.03;
  const MAX_ZOOM = 4;

  const pct = Math.round(zoom * 100);

  const handleFit = useCallback(() => {
    fitView({ duration: 400, padding: 0.14 });
    onFitView?.();
  }, [fitView, onFitView]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: 'easeOut' }}
      style={{
        position: 'fixed',
        bottom: 24,
        right: 24,
        zIndex: 50,
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        background: 'rgba(11,16,23,0.92)',
        backdropFilter: 'blur(12px)',
        WebkitBackdropFilter: 'blur(12px)',
        border: '1px solid rgba(30,41,59,0.9)',
        borderRadius: 12,
        padding: '6px 10px',
        boxShadow: '0 4px 24px rgba(0,0,0,0.55)',
      }}
    >
      {/* Zoom out */}
      <IconBtn onClick={() => zoomOut({ duration: 200 })} title="Zoom out">
        <ZoomOut size={12} />
      </IconBtn>

      {/* Slider */}
      <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
        <input
          type="range"
          min={MIN_ZOOM}
          max={MAX_ZOOM}
          step={0.01}
          value={zoom}
          onChange={e => zoomTo(parseFloat(e.target.value), { duration: 120 })}
          style={{
            width: 80,
            appearance: 'none',
            WebkitAppearance: 'none',
            height: 3,
            borderRadius: 2,
            outline: 'none',
            cursor: 'pointer',
            background: `linear-gradient(
              to right,
              #7c3aed ${((zoom - MIN_ZOOM) / (MAX_ZOOM - MIN_ZOOM)) * 100}%,
              rgba(51,65,85,0.6) ${((zoom - MIN_ZOOM) / (MAX_ZOOM - MIN_ZOOM)) * 100}%
            )`,
          }}
          className="ez-zoom-slider"
        />
      </div>

      {/* Zoom in */}
      <IconBtn onClick={() => zoomIn({ duration: 200 })} title="Zoom in">
        <ZoomIn size={12} />
      </IconBtn>

      {/* Percentage label — click to fit view */}
      <button
        onClick={handleFit}
        title="Fit view"
        style={{
          background: 'rgba(255,255,255,0.04)',
          border: '1px solid rgba(255,255,255,0.06)',
          borderRadius: 6,
          padding: '2px 7px',
          color: 'var(--t3, #475569)',
          fontSize: 9.5,
          fontFamily: 'var(--mono)',
          fontVariantNumeric: 'tabular-nums',
          cursor: 'pointer',
          letterSpacing: '.03em',
          minWidth: 38,
          textAlign: 'center',
          transition: 'color .15s, background .15s',
        }}
        onMouseEnter={e => {
          e.currentTarget.style.color = '#a78bfa';
          e.currentTarget.style.background = 'rgba(139,92,246,0.1)';
        }}
        onMouseLeave={e => {
          e.currentTarget.style.color = 'var(--t3, #475569)';
          e.currentTarget.style.background = 'rgba(255,255,255,0.04)';
        }}
      >
        {pct}%
      </button>

      {/* Fit view button */}
      <IconBtn onClick={handleFit} title="Fit all nodes">
        <Maximize2 size={11} />
      </IconBtn>
    </motion.div>
  );
}

// ── Small icon button ──────────────────────────────────────────────────────
function IconBtn({
  onClick,
  title,
  children,
}: {
  onClick: () => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: 24,
        height: 24,
        background: 'transparent',
        border: 'none',
        borderRadius: 6,
        color: 'var(--t2, #94a3b8)',
        cursor: 'pointer',
        flexShrink: 0,
        transition: 'color .15s, background .15s',
      }}
      onMouseEnter={e => {
        e.currentTarget.style.color = '#a78bfa';
        e.currentTarget.style.background = 'rgba(139,92,246,0.1)';
      }}
      onMouseLeave={e => {
        e.currentTarget.style.color = 'var(--t2, #94a3b8)';
        e.currentTarget.style.background = 'transparent';
      }}
    >
      {children}
    </button>
  );
}