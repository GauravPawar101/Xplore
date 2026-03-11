import React, { useCallback, useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Send, Mic, MicOff, Sparkles, X, Loader } from 'lucide-react';

interface PromptBarProps {
  /** Called when the user submits a query (typed or transcribed). */
  onSubmit: (query: string) => void;
  /** Optional: show a loading/streaming state */
  loading?: boolean;
  /** Optional: placeholder text */
  placeholder?: string;
}

// Extend Window for webkit speech
declare global {
  interface Window {
    SpeechRecognition: any;
    webkitSpeechRecognition: any;
  }
}

export function PromptBar({
  onSubmit,
  loading = false,
  placeholder = 'Ask anything about this codebase…',
}: PromptBarProps) {
  const [value, setValue] = useState('');
  const [listening, setListening] = useState(false);
  const [micAvailable, setMicAvailable] = useState(false);
  const [focused, setFocused] = useState(false);
  const [shimmer, setShimmer] = useState(false);

  const inputRef = useRef<HTMLTextAreaElement>(null);
  const recognitionRef = useRef<any>(null);

  // ── Check mic / speech recognition availability ───────────────────────
  useEffect(() => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    setMicAvailable(!!SR);
  }, []);

  // ── Auto-resize textarea ───────────────────────────────────────────────
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 140) + 'px';
  }, [value]);

  // ── Submit ─────────────────────────────────────────────────────────────
  const handleSubmit = useCallback(() => {
    const q = value.trim();
    if (!q || loading) return;
    setShimmer(true);
    setTimeout(() => setShimmer(false), 800);
    onSubmit(q);
    setValue('');
    if (inputRef.current) inputRef.current.style.height = 'auto';
  }, [value, loading, onSubmit]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  // ── Mic / Speech Recognition ───────────────────────────────────────────
  const toggleMic = useCallback(() => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) return;

    if (listening) {
      recognitionRef.current?.stop();
      setListening(false);
      return;
    }

    const rec = new SR();
    rec.continuous = false;
    rec.interimResults = true;
    rec.lang = 'en-US';

    rec.onstart = () => setListening(true);
    rec.onresult = (e: any) => {
      const transcript = Array.from(e.results)
        .map((r: any) => r[0].transcript)
        .join('');
      setValue(transcript);
    };
    rec.onend = () => setListening(false);
    rec.onerror = () => setListening(false);

    recognitionRef.current = rec;
    rec.start();
  }, [listening]);

  // ── Render ─────────────────────────────────────────────────────────────
  const hasValue = value.trim().length > 0;

  return (
    <div
      style={{
        position: 'fixed',
        bottom: 24,
        left: '50%',
        transform: 'translateX(-50%)',
        width: 'clamp(320px, 45vw, 680px)',
        zIndex: 50,
        pointerEvents: 'auto',
      }}
    >
      {/* Glow halo when focused */}
      <AnimatePresence>
        {focused && (
          <motion.div
            initial={{ opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.96 }}
            transition={{ duration: 0.2 }}
            style={{
              position: 'absolute',
              inset: -2,
              borderRadius: 18,
              background:
                'linear-gradient(135deg, rgba(139,92,246,0.25), rgba(59,130,246,0.15), rgba(20,184,166,0.1))',
              filter: 'blur(8px)',
              zIndex: -1,
            }}
          />
        )}
      </AnimatePresence>

      {/* Main pill */}
      <motion.div
        animate={{
          boxShadow: focused
            ? '0 8px 48px rgba(139,92,246,0.28), 0 2px 12px rgba(0,0,0,0.6)'
            : '0 4px 24px rgba(0,0,0,0.55)',
        }}
        transition={{ duration: 0.2 }}
        style={{
          display: 'flex',
          alignItems: 'flex-end',
          gap: 8,
          background: 'rgba(13,18,27,0.94)',
          backdropFilter: 'blur(20px)',
          WebkitBackdropFilter: 'blur(20px)',
          border: `1px solid ${
            focused
              ? 'rgba(139,92,246,0.55)'
              : shimmer
              ? 'rgba(20,184,166,0.6)'
              : 'rgba(30,41,59,0.9)'
          }`,
          borderRadius: 16,
          padding: '10px 10px 10px 16px',
          transition: 'border-color 0.2s',
          position: 'relative',
          overflow: 'hidden',
        }}
      >
        {/* Shimmer sweep on submit */}
        <AnimatePresence>
          {shimmer && (
            <motion.div
              initial={{ x: '-100%' }}
              animate={{ x: '200%' }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.7, ease: 'easeInOut' }}
              style={{
                position: 'absolute',
                inset: 0,
                background:
                  'linear-gradient(90deg, transparent, rgba(139,92,246,0.18), transparent)',
                pointerEvents: 'none',
                zIndex: 0,
              }}
            />
          )}
        </AnimatePresence>

        {/* Sparkle icon */}
        <div
          style={{
            flexShrink: 0,
            marginBottom: 6,
            color: focused ? '#a78bfa' : 'var(--t4, #334155)',
            transition: 'color 0.2s',
          }}
        >
          <Sparkles size={15} />
        </div>

        {/* Textarea */}
        <textarea
          ref={inputRef}
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          placeholder={listening ? 'Listening…' : placeholder}
          rows={1}
          style={{
            flex: 1,
            background: 'transparent',
            border: 'none',
            outline: 'none',
            resize: 'none',
            color: 'var(--t1, #e2e8f0)',
            fontSize: 13,
            fontFamily: 'var(--ui, system-ui)',
            lineHeight: 1.55,
            padding: '2px 0',
            minHeight: 24,
            maxHeight: 140,
            overflowY: 'auto',
            caretColor: '#a78bfa',
            zIndex: 1,
          }}
        />

        {/* Right actions */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 4,
            flexShrink: 0,
            marginBottom: 2,
            zIndex: 1,
          }}
        >
          {/* Clear */}
          <AnimatePresence>
            {hasValue && !loading && (
              <motion.button
                initial={{ opacity: 0, scale: 0.7 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.7 }}
                transition={{ duration: 0.12 }}
                onClick={() => { setValue(''); inputRef.current?.focus(); }}
                style={iconBtnStyle}
                title="Clear"
              >
                <X size={12} />
              </motion.button>
            )}
          </AnimatePresence>

          {/* Mic */}
          {micAvailable && (
            <motion.button
              animate={{
                background: listening
                  ? 'rgba(239,68,68,0.18)'
                  : 'rgba(255,255,255,0.04)',
                borderColor: listening
                  ? 'rgba(239,68,68,0.6)'
                  : 'rgba(255,255,255,0.08)',
              }}
              transition={{ duration: 0.15 }}
              onClick={toggleMic}
              title={listening ? 'Stop listening' : 'Voice input'}
              style={{
                ...iconBtnStyle,
                border: '1px solid rgba(255,255,255,0.08)',
                position: 'relative',
              }}
            >
              {/* Pulse ring when listening */}
              <AnimatePresence>
                {listening && (
                  <motion.span
                    initial={{ opacity: 0.8, scale: 1 }}
                    animate={{ opacity: 0, scale: 2.2 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 1, repeat: Infinity }}
                    style={{
                      position: 'absolute',
                      inset: 0,
                      borderRadius: '50%',
                      border: '1px solid rgba(239,68,68,0.5)',
                      pointerEvents: 'none',
                    }}
                  />
                )}
              </AnimatePresence>
              {listening
                ? <MicOff size={13} style={{ color: '#f87171' }} />
                : <Mic size={13} style={{ color: 'var(--t2, #94a3b8)' }} />
              }
            </motion.button>
          )}

          {/* Send */}
          <motion.button
            animate={{
              background: hasValue && !loading
                ? 'linear-gradient(135deg, #7c3aed, #4f46e5)'
                : 'rgba(255,255,255,0.05)',
              opacity: hasValue || loading ? 1 : 0.4,
            }}
            transition={{ duration: 0.15 }}
            onClick={handleSubmit}
            disabled={!hasValue || loading}
            title="Send (Enter)"
            style={{
              ...iconBtnStyle,
              width: 32,
              height: 32,
              borderRadius: 10,
              color: hasValue && !loading ? '#fff' : 'var(--t3)',
            }}
          >
            {loading
              ? <Loader size={13} style={{ animation: 'spin 1s linear infinite' }} />
              : <Send size={13} />
            }
          </motion.button>
        </div>
      </motion.div>

      {/* Hint */}
      <AnimatePresence>
        {focused && !hasValue && (
          <motion.div
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 4 }}
            transition={{ duration: 0.15, delay: 0.1 }}
            style={{
              textAlign: 'center',
              fontSize: 9.5,
              color: 'var(--t4, #334155)',
              marginTop: 6,
              fontFamily: 'var(--mono)',
              letterSpacing: '.04em',
            }}
          >
            Enter to send · Shift+Enter for new line
            {micAvailable && ' · 🎤 voice input available'}
          </motion.div>
        )}
      </AnimatePresence>

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        textarea::placeholder { color: var(--t4, #334155); }
      `}</style>
    </div>
  );
}

// ── Shared icon button base style ──────────────────────────────────────────
const iconBtnStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  width: 30,
  height: 30,
  borderRadius: 9,
  border: 'none',
  background: 'rgba(255,255,255,0.04)',
  color: 'var(--t2, #94a3b8)',
  cursor: 'pointer',
  flexShrink: 0,
  transition: 'all .15s',
};