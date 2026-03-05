import { SignedIn, SignedOut } from '@clerk/clerk-react';
import { Navigate, useLocation } from 'react-router-dom';

/**
 * Renders children only when signed in; otherwise redirects to landing.
 */
export default function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  return (
    <>
      <SignedIn>{children}</SignedIn>
      <SignedOut>
        <Navigate to="/" state={{ from: location }} replace />
      </SignedOut>
    </>
  );
}
