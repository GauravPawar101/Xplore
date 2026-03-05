import { SignedIn, SignedOut, SignInButton, SignUpButton } from '@clerk/clerk-react';
import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';

/**
 * Landing page: product description only (no file paths or metadata).
 * Sign in required to access the app.
 */
function RedirectToApp() {
  const navigate = useNavigate();
  useEffect(() => {
    navigate('/app', { replace: true });
  }, [navigate]);
  return null;
}

export default function LandingPage() {
  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'var(--c0)',
        color: 'var(--t1)',
        fontFamily: 'var(--ui)',
        padding: 24,
      }}
    >
      <SignedIn>
        <RedirectToApp />
      </SignedIn>
      <SignedOut>
        <div style={{ maxWidth: 420, textAlign: 'center' }}>
          <h1 style={{ fontSize: 28, fontWeight: 700, letterSpacing: '-.02em', marginBottom: 12 }}>
            EzDocs
          </h1>
          <p style={{ fontSize: 15, color: 'var(--t2)', lineHeight: 1.6, marginBottom: 24 }}>
            Turn codebases into interactive dependency graphs, define your program as a graph of intents,
            and generate code from it. Sign in to get started.
          </p>
          <div style={{ display: 'flex', gap: 12, justifyContent: 'center', flexWrap: 'wrap' }}>
            <SignInButton mode="modal">
              <button
                type="button"
                style={{
                  padding: '10px 20px',
                  fontSize: 14,
                  fontWeight: 600,
                  background: 'var(--bl)',
                  color: '#fff',
                  border: 'none',
                  borderRadius: 8,
                  cursor: 'pointer',
                  boxShadow: '0 0 14px rgba(59,130,246,.35)',
                }}
              >
                Sign in
              </button>
            </SignInButton>
            <SignUpButton mode="modal">
              <button
                type="button"
                style={{
                  padding: '10px 20px',
                  fontSize: 14,
                  fontWeight: 600,
                  background: 'transparent',
                  color: 'var(--t1)',
                  border: '1px solid var(--ln)',
                  borderRadius: 8,
                  cursor: 'pointer',
                }}
              >
                Sign up
              </button>
            </SignUpButton>
          </div>
        </div>
      </SignedOut>
    </div>
  );
}
