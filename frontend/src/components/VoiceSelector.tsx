import React, { useEffect, useRef, useState } from 'react';
import { Volume2, ChevronDown, Wifi, Check } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

interface VoiceSelectorProps {
  selectedVoice: string | null;
  onVoiceChange: (voiceName: string | null) => void;
}

interface GroupedVoices {
  local: SpeechSynthesisVoice[];
  remote: SpeechSynthesisVoice[];
}

export function VoiceSelector({ selectedVoice, onVoiceChange }: VoiceSelectorProps) {
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Load voices — Chrome fires onvoiceschanged async, Firefox/Safari sync
  useEffect(() => {
    const load = () => {
      const v = window.speechSynthesis?.getVoices() ?? [];
      if (v.length) setVoices(v);
    };
    load();
    window.speechSynthesis.onvoiceschanged = load;
    return () => { window.speechSynthesis.onvoiceschanged = null; };
  }, []);

  // Close dropdown when clicking outside
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  if (!voices.length) return null;

  const grouped: GroupedVoices = voices.reduce<GroupedVoices>(
    (acc, v) => {
      acc[v.localService ? 'local' : 'remote'].push(v);
      return acc;
    },
    { local: [], remote: [] }
  );

  const activeVoice = voices.find(v => v.name === selectedVoice);
  const label = activeVoice
    ? activeVoice.name.length > 22
      ? activeVoice.name.slice(0, 21) + '…'
      : activeVoice.name
    : 'Auto';

  return (
    <div
      ref={ref}
      style={{
        position: 'fixed',
        top: 10,
        right: 10,
        zIndex: 9999,
        fontFamily: 'var(--mono)',
      }}
    >
      {/* Trigger button */}
      <button
        onClick={() => setOpen(p => !p)}
        title="Select TTS voice"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          padding: '5px 10px',
          background: open
            ? 'rgba(139,92,246,0.15)'
            : 'rgba(11,16,23,0.92)',
          backdropFilter: 'blur(10px)',
          WebkitBackdropFilter: 'blur(10px)',
          border: `1px solid ${open ? 'rgba(139,92,246,0.5)' : 'var(--ln2, #1e293b)'}`,
          borderRadius: open ? '8px 8px 0 0' : 8,
          color: open ? '#a78bfa' : 'var(--t2, #94a3b8)',
          cursor: 'pointer',
          fontSize: 10,
          fontFamily: 'var(--mono)',
          transition: 'all .15s',
          whiteSpace: 'nowrap',
          boxShadow: open ? 'none' : '0 4px 20px rgba(0,0,0,0.5)',
        }}
      >
        <Volume2
          size={11}
          style={{ color: open ? '#a78bfa' : 'var(--t3, #475569)', flexShrink: 0 }}
        />
        <span>{label}</span>
        <ChevronDown
          size={10}
          style={{
            color: 'var(--t3, #475569)',
            transform: open ? 'rotate(180deg)' : 'rotate(0deg)',
            transition: 'transform .2s',
          }}
        />
      </button>

      {/* Dropdown panel */}
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -4, scaleY: 0.96 }}
            animate={{ opacity: 1, y: 0, scaleY: 1 }}
            exit={{ opacity: 0, y: -4, scaleY: 0.96 }}
            transition={{ duration: 0.12, ease: 'easeOut' }}
            style={{
              position: 'absolute',
              top: '100%',
              right: 0,
              width: 260,
              maxHeight: 340,
              overflowY: 'auto',
              background: 'rgba(11,16,23,0.97)',
              backdropFilter: 'blur(16px)',
              WebkitBackdropFilter: 'blur(16px)',
              border: '1px solid rgba(139,92,246,0.4)',
              borderTop: 'none',
              borderRadius: '0 0 10px 10px',
              boxShadow: '0 16px 48px rgba(0,0,0,0.7)',
              transformOrigin: 'top',
            }}
          >
            {/* Auto option */}
            <VoiceOption
              label="Auto (best available)"
              sublabel="Neural · system preference"
              isSelected={!selectedVoice}
              showWifi={false}
              onClick={() => { onVoiceChange(null); setOpen(false); }}
            />

            {/* Local voices */}
            {grouped.local.length > 0 && (
              <GroupHeader label="On-device" />
            )}
            {grouped.local.map(v => (
              <VoiceOption
                key={v.name}
                label={v.name}
                sublabel={v.lang}
                isSelected={selectedVoice === v.name}
                showWifi={false}
                onClick={() => { onVoiceChange(v.name); setOpen(false); }}
              />
            ))}

            {/* Remote voices */}
            {grouped.remote.length > 0 && (
              <GroupHeader
                label="Cloud"
                icon={<Wifi size={9} style={{ color: 'var(--t3)' }} />}
              />
            )}
            {grouped.remote.map(v => (
              <VoiceOption
                key={v.name}
                label={v.name}
                sublabel={v.lang}
                isSelected={selectedVoice === v.name}
                showWifi={true}
                onClick={() => { onVoiceChange(v.name); setOpen(false); }}
              />
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ── Sub-components ──────────────────────────────────────────────────────────

function GroupHeader({ label, icon }: { label: string; icon?: React.ReactNode }) {
  return (
    <div
      style={{
        padding: '7px 12px 4px',
        fontSize: 8.5,
        fontWeight: 700,
        letterSpacing: '.1em',
        textTransform: 'uppercase',
        color: 'var(--t4, #334155)',
        display: 'flex',
        alignItems: 'center',
        gap: 5,
        borderTop: '1px solid var(--ln, #0f172a)',
      }}
    >
      {icon}
      {label}
    </div>
  );
}

interface VoiceOptionProps {
  label: string;
  sublabel: string;
  isSelected: boolean;
  showWifi: boolean;
  onClick: () => void;
}

function VoiceOption({ label, sublabel, isSelected, showWifi, onClick }: VoiceOptionProps) {
  const [hovered, setHovered] = useState(false);
  const displayLabel = label.length > 28 ? label.slice(0, 27) + '…' : label;

  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        width: '100%',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '7px 12px',
        background: isSelected
          ? 'rgba(139,92,246,0.14)'
          : hovered
          ? 'rgba(255,255,255,0.04)'
          : 'transparent',
        border: 'none',
        cursor: 'pointer',
        textAlign: 'left',
        transition: 'background .1s',
      }}
    >
      {/* Checkmark or spacer */}
      <span style={{ width: 14, flexShrink: 0, display: 'flex', alignItems: 'center' }}>
        {isSelected && <Check size={11} style={{ color: '#a78bfa' }} />}
      </span>

      {/* Name + language */}
      <span style={{ flex: 1, overflow: 'hidden' }}>
        <span
          style={{
            display: 'block',
            fontSize: 10,
            fontFamily: 'var(--mono)',
            color: isSelected ? '#a78bfa' : 'var(--t1, #e2e8f0)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {displayLabel}
        </span>
        <span
          style={{
            display: 'block',
            fontSize: 8.5,
            color: 'var(--t4, #334155)',
            marginTop: 1,
          }}
        >
          {sublabel}
        </span>
      </span>

      {/* Cloud indicator */}
      {showWifi && (
        <Wifi size={9} style={{ color: 'var(--t4, #334155)', flexShrink: 0 }} />
      )}
    </button>
  );
}