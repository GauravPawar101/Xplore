import React, { createContext, useContext, useState, useCallback, useRef } from 'react';

type TourContextValue = {
  isNarrating: boolean;
  setNarrating: (v: boolean) => void;
  registerNarratorWs: (ws: WebSocket | null) => void;
  stopNarration: () => void;
};

const TourContext = createContext<TourContextValue | null>(null);

export function TourProvider({ children }: { children: React.ReactNode }) {
  const [isNarrating, setNarrating] = useState(false);
  const narratorWsRef = useRef<WebSocket | null>(null);

  const registerNarratorWs = useCallback((ws: WebSocket | null) => {
    narratorWsRef.current = ws;
  }, []);

  const stopNarration = useCallback(() => {
    if (narratorWsRef.current) {
      try {
        narratorWsRef.current.close();
      } catch {
        // ignore
      }
      narratorWsRef.current = null;
    }
    setNarrating(false);
  }, []);

  const value: TourContextValue = {
    isNarrating,
    setNarrating,
    registerNarratorWs,
    stopNarration,
  };

  return <TourContext.Provider value={value}>{children}</TourContext.Provider>;
}

export function useTour() {
  const ctx = useContext(TourContext);
  if (!ctx) return { isNarrating: false, setNarrating: () => {}, registerNarratorWs: () => {}, stopNarration: () => {} };
  return ctx;
}
