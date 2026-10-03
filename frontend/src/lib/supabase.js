import { createClient } from "@supabase/supabase-js";

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

/**
 * "supabase" (default) requires a signed-in Supabase session.
 * "off" is an explicit local-development mode and must be paired with
 * CADASTRA_AUTH=off on the backend.
 */
export const AUTH_MODE =
  String(import.meta.env.VITE_AUTH_MODE || "supabase").toLowerCase() === "off"
    ? "off"
    : "supabase";

export const supabaseConfigured = Boolean(supabaseUrl && supabaseAnonKey);

// Only the public anon key is used in the browser. The session is kept in
// localStorage and refreshed automatically, so it survives a reload.
export const supabase = supabaseConfigured
  ? createClient(supabaseUrl, supabaseAnonKey, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
      },
    })
  : null;
