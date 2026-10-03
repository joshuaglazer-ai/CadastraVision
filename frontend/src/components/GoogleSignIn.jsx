import { useState } from "react";
import { useAuth } from "../context/AuthContext";
import { oauthMessage } from "../lib/signup";
import Icon from "./Icon";

function GoogleMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.5 0 6.6 1.2 9.1 3.6l6.8-6.8C35.8 2.4 30.3 0 24 0 14.6 0 6.6 5.4 2.7 13.3l7.9 6.1C12.5 13.6 17.8 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.1 24.5c0-1.6-.1-3.1-.4-4.5H24v9h12.4c-.5 2.9-2.2 5.3-4.6 7l7.4 5.7c4.3-4 6.9-9.9 6.9-17.2z" />
      <path fill="#FBBC05" d="M10.6 28.6A14.5 14.5 0 0 1 9.5 24c0-1.6.3-3.2.8-4.6l-7.9-6.1A24 24 0 0 0 0 24c0 3.9.9 7.5 2.6 10.7l8-6.1z" />
      <path fill="#34A853" d="M24 48c6.5 0 11.9-2.1 15.9-5.8l-7.4-5.7c-2.1 1.4-4.8 2.3-8.5 2.3-6.2 0-11.5-4.1-13.4-9.9l-8 6.1C6.6 42.6 14.6 48 24 48z" />
    </svg>
  );
}

/**
 * "Continue with Google". The browser goes to Google and comes back to
 * /login; a new account is then asked for its government surveyor ID.
 */
export default function GoogleSignIn({ disabled = false }) {
  const { signInWithGoogle } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function handleClick() {
    setBusy(true);
    setError("");
    try {
      await signInWithGoogle();
      // On success the page navigates away to Google.
    } catch (err) {
      setError(oauthMessage(err));
      setBusy(false);
    }
  }

  return (
    <div className="oauth">
      <div className="oauth__divider" aria-hidden="true">
        <span>or</span>
      </div>
      <button type="button" className="btn btn--secondary btn--block oauth__google" onClick={handleClick} disabled={disabled || busy}>
        <GoogleMark />
        {busy ? "Opening Google" : "Continue with Google"}
      </button>
      {error ? (
        <div className="notice notice--error" role="alert">
          <Icon name="alert" size={16} />
          <p>{error}</p>
        </div>
      ) : null}
    </div>
  );
}
