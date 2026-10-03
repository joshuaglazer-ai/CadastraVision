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

  /**
   * Create a surveyor account. Resolves to { session, needsConfirmation }:
   * a session when the project signs new users in straight away, otherwise
   * needsConfirmation and the user must open the e-mailed link first.
   */
  const signUp = useCallback(async (name, email, password, govtId) => {
    if (AUTH_MODE === "off") {
      throw new Error("Account creation is unavailable in development mode (VITE_AUTH_MODE=off).");
    }
    if (!supabase) {
      throw new Error(
        "Account creation is not configured. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY in frontend/.env."
      );
    }
    const { data, error } = await supabase.auth.signUp({
      email,
      password,
      options: {
        // The government surveyor ID is self-declared: it is stored in the
        // account's profile and shown as such, never treated as verified.
        data: { full_name: name, govt_surveyor_id: govtId },
        emailRedirectTo: `${window.location.origin}/login`,
      },
    });
    if (error) throw error;
    // With e-mail confirmation on, Supabase does not reveal an existing
    // account as an error: it returns a user with no identities instead.
    if (!data.session && data.user && Array.isArray(data.user.identities) && data.user.identities.length === 0) {
      const existing = new Error("User already registered");
      existing.code = "user_already_exists";
      throw existing;
    }
    if (data.session) setSession(data.session);
    return { session: data.session ?? null, needsConfirmation: !data.session };
  }, []);

  /** Start Google sign-in. The browser leaves for Google and returns to /login. */
  const signInWithGoogle = useCallback(async () => {
    if (AUTH_MODE === "off") {
      throw new Error("Google sign-in is unavailable in development mode (VITE_AUTH_MODE=off).");
    }
    if (!supabase) {
      throw new Error(
        "Sign-in is not configured. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY in frontend/.env."
      );
    }
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${window.location.origin}/login` },
    });
    if (error) throw error;
  }, []);

  /**
   * Save the surveyor's name and self-declared government surveyor ID in
   * their Supabase profile. The session is refreshed afterwards so the next
   * API request carries a token the server has not cached with the old
   * profile.
   */
  const updateProfile = useCallback(async ({ name, govtId }) => {
    if (!supabase) throw new Error("Sign-in is not configured.");
    const data = { govt_surveyor_id: govtId };
    if (name) data.full_name = name;
    const { error } = await supabase.auth.updateUser({ data });
    if (error) throw error;
    const refreshed = await supabase.auth.refreshSession();
    if (refreshed.error) throw refreshed.error;
    setSession(refreshed.data.session);
    return refreshed.data.session;
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
      // Every account must have entered a government surveyor ID before it
      // can open the workspace (development mode has no profile).
      profileComplete:
        AUTH_MODE === "off" || Boolean(String(session?.user?.user_metadata?.govt_surveyor_id || "").trim()),
      recovery,
      signIn,
      signUp,
      signInWithGoogle,
      updateProfile,
      signOut,
      requestPasswordReset,
      updatePassword,
    }),
    [loading, session, recovery, signIn, signUp, signInWithGoogle, updateProfile, signOut, requestPasswordReset, updatePassword]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>");
  return context;
}
