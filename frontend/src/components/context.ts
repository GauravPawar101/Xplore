import React from 'react';
import { MemberData } from '@/types';

export const MemberClickCtx = React.createContext<(m: MemberData) => void>(() => { });

export const FocusCtx = React.createContext<{
    focusId: string | null;
    outSet: Set<string>;
    inSet: Set<string>;
}>({ focusId: null, outSet: new Set(), inSet: new Set() });
