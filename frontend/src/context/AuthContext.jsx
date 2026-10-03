import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { AUTH_MODE, supabase, supabaseConfigured } from "../lib/supabase";
import { setUnauthorizedHandler } from "../lib/api";

const AuthContext = createContext(null);

/**
 * Session state for the whole app.
 *
 * With Supabase, the session is persisted by supabase-js and restored on
 * reload. In the explicit development mode (VITE_AUTH_MODE=off) there is no
 * sign-in and the backend labels the session as a dev session.
 */
export function AuthProvider({ children }) {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(AUTH_MODE !== "off" && supabaseConfigured);
  const [recovery, setRecovery] = useState(false);

  useEffect(() => {
    if (AUTH_MODE === "off" || !supabase) {
      setLoading(false);
      return undefined;
    }

    let active = true;
    supabase.auth.getSession().then(({ data }) => {
      if (!active) return;
      setSession(data?.session ?? null);
      setLoading(false);
    });

    const { data } = supabase.auth.onAuthStateChange((event, next) => {
      setSession(next ?? null);
      if (event === "PASSWORD_RECOVERY") setRecovery(true);
      if (event === "SIGNED_OUT") setRecovery(false);
    });

    return () => {
      active = false;
      data?.subscription?.unsubscribe();
    };
  }, []);

  const signIn = useCallback(async (email, password) => {
    if (!supabase) {
      throw new Error(
        "Sign-in is not configured. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY in frontend/.env."
      );
    }
    const { data, error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) throw error;
    setSession(data.session);
    return data.session;
  }, []);

  const signOut = useCallback(async () => {
    if (supabase) await supabase.auth.signOut();
    setSession(null);
  }, []);

  const requestPasswordReset = useCallback(async (email) => {
    if (!supabase) throw new Error("Sign-in is not configured.");
    const { error } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: `${window.location.origin}/reset-password`,
    });
    if (error) throw error;
  }, []);

  const updatePassword = useCallback(async (password) => {
    if (!supabase) throw new Error("Sign-in is not configured.");
    const { error } = await supabase.auth.updateUser({ password });
    if (error) throw error;
    setRecovery(false);
  }, []);

  // An expired or revoked token ends the session instead of leaving the
  // user in front of failing requests.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      if (AUTH_MODE !== "off") signOut();
    });
    return () => setUnauthorizedHandler(null);
  }, [signOut]);

  const value = useMemo(
    () => ({
      mode: AUTH_MODE,
      configured: AUTH_MODE === "off" || supabaseConfigured,
      loading,
      session,
      user: session?.user ?? null,
      authenticated: AUTH_MODE === "off" || Boolean(session),
      recovery,
      signIn,
      signOut,
      requestPasswordReset,
      updatePassword,
    }),
    [loading, session, recovery, signIn, signOut, requestPasswordReset, updatePassword]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>");
  return context;
}
