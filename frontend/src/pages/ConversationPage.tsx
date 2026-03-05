import { useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { MessageCircle } from 'lucide-react';
import { WS_BASE } from '@/config/constants';

type Message = { role: 'user' | 'assistant'; content: string };

export default function ConversationPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  // One long-lived WebSocket; server keeps conversation state per connection.
  useEffect(() => {
    const ws = new WebSocket(`${WS_BASE}/ws/chat`);
    wsRef.current = ws;
    ws.onopen = () => {
      setConnected(true);
      setError(null);
    };
    ws.onclose = () => {
      setConnected(false);
      setStreaming(false);
      wsRef.current = null;
    };
    ws.onerror = () => {
      setError('Connection failed. Is the backend running?');
      setStreaming(false);
    };
    ws.onmessage = (ev) => {
      const data = ev.data;
      if (typeof data !== 'string') return;
      if (data === '\x01') {
        setStreaming(false);
        return;
      }
      if (data.startsWith('{')) {
        try {
          const parsed = JSON.parse(data);
          if (parsed.error) {
            setError(parsed.error);
            setStreaming(false);
            return;
          }
        } catch {
          // not JSON, treat as content
        }
      }
      setMessages((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last?.role === 'assistant') {
          next[next.length - 1] = { ...last, content: last.content + data };
        } else {
          next.push({ role: 'assistant', content: data });
        }
        return next;
      });
    };
    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, []);

  const send = useCallback(() => {
    const text = input.trim();
    if (!text || streaming) return;
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      setError('Not connected. Refresh the page.');
      return;
    }

    const userMessage: Message = { role: 'user', content: text };
    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    setError(null);
    setStreaming(true);
    wsRef.current.send(text);
  }, [input, streaming]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        send();
      }
    },
    [send]
  );

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        background: 'var(--c0)',
        color: 'var(--t1)',
        fontFamily: 'var(--ui)',
      }}
    >
      <div
        style={{
          padding: '16px 24px',
          borderBottom: '1px solid var(--ln)',
          display: 'flex',
          alignItems: 'center',
          gap: 10,
        }}
      >
        <MessageCircle size={20} style={{ color: 'var(--bl)' }} />
        <h1 style={{ fontSize: 18, fontWeight: 700 }}>Conversation</h1>
        <span style={{ fontSize: 11, color: 'var(--t3)', marginLeft: 8 }}>
          {connected ? 'Connected' : 'Disconnected'}
        </span>
      </div>

      <div
        style={{
          flex: 1,
          overflow: 'auto',
          padding: 24,
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
        }}
      >
        {messages.length === 0 && !error && (
          <div
            style={{
              textAlign: 'center',
              color: 'var(--t3)',
              fontSize: 14,
              padding: '48px 24px',
            }}
          >
            Send a message to start. Ask about code, architecture, or the codebase.
          </div>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            style={{
              alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start',
              maxWidth: '85%',
              padding: '12px 16px',
              borderRadius: 12,
              background: m.role === 'user' ? 'var(--bl)' : 'var(--c2)',
              border: '1px solid var(--ln)',
            }}
          >
            {m.role === 'assistant' ? (
              <div style={{ fontSize: 13, lineHeight: 1.55 }}>
                <ReactMarkdown>{m.content}</ReactMarkdown>
              </div>
            ) : (
              <span style={{ fontSize: 13, whiteSpace: 'pre-wrap' }}>{m.content}</span>
            )}
          </div>
        ))}
        {streaming && messages[messages.length - 1]?.role === 'user' && (
          <div
            style={{
              alignSelf: 'flex-start',
              padding: '12px 16px',
              borderRadius: 12,
              background: 'var(--c2)',
              border: '1px solid var(--ln)',
              fontSize: 13,
              color: 'var(--t3)',
            }}
          >
            …
          </div>
        )}
        {error && (
          <div
            style={{
              padding: 12,
              borderRadius: 8,
              background: 'rgba(244,63,94,.1)',
              border: '1px solid rgba(244,63,94,.25)',
              color: '#fda4af',
              fontSize: 13,
            }}
          >
            {error}
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div
        style={{
          padding: 16,
          borderTop: '1px solid var(--ln)',
          background: 'var(--c1)',
        }}
      >
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end' }}>
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type a message…"
            disabled={streaming}
            rows={1}
            style={{
              flex: 1,
              minHeight: 44,
              maxHeight: 120,
              padding: '10px 14px',
              borderRadius: 8,
              border: '1px solid var(--ln)',
              background: 'var(--c2)',
              color: 'var(--t1)',
              fontSize: 14,
              fontFamily: 'var(--ui)',
              resize: 'none',
            }}
          />
          <button
            type="button"
            onClick={send}
            disabled={streaming || !input.trim()}
            style={{
              padding: '10px 20px',
              borderRadius: 8,
              border: 'none',
              background: streaming ? 'var(--c3)' : 'var(--bl)',
              color: '#fff',
              fontSize: 14,
              fontWeight: 600,
              cursor: streaming ? 'not-allowed' : 'pointer',
            }}
          >
            {streaming ? '…' : 'Send'}
          </button>
        </div>
      </div>
    </div>
  );
}
