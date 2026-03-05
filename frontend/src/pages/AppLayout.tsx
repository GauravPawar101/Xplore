import { Outlet, NavLink, useNavigate, useLocation } from 'react-router-dom';
import { UserButton } from '@clerk/clerk-react';
import { Search, GitBranch, MessageCircle, Volume2, X } from 'lucide-react';
import { useTour } from '@/context/TourContext';

export default function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { isNarrating, stopNarration } = useTour();
  const isOnIDE = location.pathname === '/app' || location.pathname === '/app/';
  const showActiveTourBanner = isNarrating && !isOnIDE;

  const navLinkStyle = ({ isActive }: { isActive: boolean }) => ({
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '8px 14px',
    borderRadius: 6,
    fontSize: 13,
    fontWeight: 500,
    color: isActive ? 'var(--t1)' : 'var(--t3)',
    background: isActive ? 'rgba(59,130,246,.12)' : 'transparent',
    textDecoration: 'none',
    border: 'none',
    cursor: 'pointer',
    fontFamily: 'var(--ui)',
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: 'var(--c0)' }}>
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 16,
          padding: '10px 16px',
          borderBottom: '1px solid var(--ln)',
          background: 'var(--bld)',
          flexShrink: 0,
        }}
      >
        <button
          type="button"
          onClick={() => navigate('/app')}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            color: 'var(--t1)',
            fontFamily: 'var(--ui)',
            fontWeight: 700,
            fontSize: 15,
          }}
        >
          <Search size={18} color="var(--bl)" />
          EzDocs
        </button>
        <nav style={{ display: 'flex', gap: 4 }}>
          <NavLink to="/app" end style={navLinkStyle}>
            <GitBranch size={14} />
            IDE
          </NavLink>
          <NavLink to="/app/graphs" style={navLinkStyle}>
            My graphs
          </NavLink>
          <NavLink to="/app/conversation" style={navLinkStyle}>
            <MessageCircle size={14} />
            Conversation
          </NavLink>
        </nav>
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center' }}>
          <UserButton afterSignOutUrl="/" />
        </div>
      </header>
      {showActiveTourBanner && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '8px 16px',
            background: 'linear-gradient(90deg, rgba(139,92,246,0.2), rgba(139,92,246,0.08))',
            borderBottom: '1px solid rgba(139,92,246,0.35)',
            fontSize: 13,
            color: '#a78bfa',
            flexShrink: 0,
          }}
        >
          <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Volume2 size={16} className="ez-spin" />
            <strong>Tour in progress</strong> — switch to IDE to follow or stop the tour.
          </span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              type="button"
              onClick={() => navigate('/app')}
              style={{
                padding: '4px 12px',
                borderRadius: 6,
                border: '1px solid rgba(139,92,246,0.5)',
                background: 'rgba(139,92,246,0.2)',
                color: '#c4b5fd',
                cursor: 'pointer',
                fontSize: 12,
                fontWeight: 600,
              }}
            >
              Go to IDE
            </button>
            <button
              type="button"
              onClick={stopNarration}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                padding: '4px 12px',
                borderRadius: 6,
                border: '1px solid rgba(244,63,94,0.4)',
                background: 'rgba(244,63,94,0.15)',
                color: '#fda4af',
                cursor: 'pointer',
                fontSize: 12,
                fontWeight: 600,
              }}
            >
              <X size={14} /> Stop tour
            </button>
          </div>
        </div>
      )}
      <main style={{ flex: 1, overflow: 'hidden' }}>
        <Outlet />
      </main>
    </div>
  );
}
