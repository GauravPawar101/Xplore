import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '@clerk/clerk-react';
import axios from 'axios';
import { GitBranch, ExternalLink } from 'lucide-react';
import { API_BASE } from '@/config/constants';

type GraphItem = { program_id: string; name: string; created_at: string | null };

export default function MyGraphsPage() {
  const { getToken } = useAuth();
  const [list, setList] = useState<GraphItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const token = await getToken();
        const res = await axios.get<GraphItem[]>(`${API_BASE}/program/list`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!cancelled) setList(res.data || []);
      } catch (e: unknown) {
        if (!cancelled) {
          const msg = axios.isAxiosError(e) && e.response?.data?.detail
            ? String(e.response.data.detail)
            : 'Failed to load your graphs.';
          setError(msg);
          setList([]);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [getToken]);

  return (
    <div
      style={{
        padding: 24,
        overflow: 'auto',
        height: '100%',
        background: 'var(--c0)',
        color: 'var(--t1)',
        fontFamily: 'var(--ui)',
      }}
    >
      <h1 style={{ fontSize: 20, fontWeight: 700, marginBottom: 8 }}>My graphs</h1>
      <p style={{ fontSize: 13, color: 'var(--t2)', marginBottom: 20 }}>
        Program graphs you created. Open one in the IDE to edit or generate code.
      </p>

      {loading && (
        <p style={{ color: 'var(--t3)', fontSize: 13 }}>Loading…</p>
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
            marginBottom: 16,
          }}
        >
          {error}
        </div>
      )}
      {!loading && !error && list.length === 0 && (
        <p style={{ color: 'var(--t3)', fontSize: 13 }}>
          No graphs yet. Create one in the IDE by defining a program graph and saving it.
        </p>
      )}
      {!loading && list.length > 0 && (
        <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {list.map((g) => (
            <li key={g.program_id}>
              <Link
                to="/app"
                state={{ programId: g.program_id }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 12,
                  padding: '12px 16px',
                  borderRadius: 8,
                  background: 'var(--c2)',
                  border: '1px solid var(--ln)',
                  color: 'var(--t1)',
                  textDecoration: 'none',
                  fontSize: 14,
                  fontWeight: 500,
                }}
              >
                <GitBranch size={18} style={{ color: 'var(--bl)', flexShrink: 0 }} />
                <span style={{ flex: 1 }}>{g.name || g.program_id}</span>
                {g.created_at && (
                  <span style={{ fontSize: 12, color: 'var(--t3)', fontVariantNumeric: 'tabular-nums' }}>
                    {new Date(g.created_at).toLocaleDateString()}
                  </span>
                )}
                <ExternalLink size={14} style={{ color: 'var(--t3)', flexShrink: 0 }} />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
