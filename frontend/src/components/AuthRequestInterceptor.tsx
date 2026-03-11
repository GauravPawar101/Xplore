/**
 * Attaches Clerk JWT to every axios request to the API when the user is signed in.
 * Mount once inside ClerkProvider so useAuth() is available.
 */
import { useEffect, useRef } from 'react';
import { useAuth } from '@clerk/clerk-react';
import axios, { InternalAxiosRequestConfig } from 'axios';
import { API_BASE } from '@/config/constants';

export default function AuthRequestInterceptor() {
  const { getToken } = useAuth();
  const interceptorId = useRef<number | null>(null);

  useEffect(() => {
    const attach = async (
      config: InternalAxiosRequestConfig
    ): Promise<InternalAxiosRequestConfig> => {
      const url = String(config.url ?? '');
      if (!url.startsWith(API_BASE)) return config;
      try {
        const token = await getToken();
        if (token) {
          // AxiosHeaders supports index assignment; cast avoids the
          // "Record<string,string> not assignable to AxiosRequestHeaders" error.
          (config.headers as Record<string, string>).Authorization = `Bearer ${token}`;
        }
      } catch {
        // ignore — request proceeds without auth header
      }
      return config;
    };

    interceptorId.current = axios.interceptors.request.use(attach);
    return () => {
      if (interceptorId.current !== null) {
        axios.interceptors.request.eject(interceptorId.current);
        interceptorId.current = null;
      }
    };
  }, [getToken]);

  return null;
}