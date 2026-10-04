import { useId, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import Globe from "../components/Globe";
import Icon from "../components/Icon";
import PublicNav from "../components/PublicNav";
import { LoadingState } from "../components/States";
import { useAuth } from "../context/AuthContext";

/**
 * Landing page of the password-reset e-mail. Supabase signs the user in
 * from the link; the new password is then set for that session.
 */
export default function ResetPassword() {
  const navigate = useNavigate();
  const { loading, session, updatePassword } = useAuth();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const passwordId = useId();
  const confirmId = useId();

  async function handleSubmit(event) {
    event.preventDefault();
    if (password.length < 8) {
      setError("Use at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setError("The two passwords do not match.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await updatePassword(password);
      setDone(true);
      window.setTimeout(() => navigate("/dashboard", { replace: true }), 1500);
    } catch (err) {
      setError(err?.message || "The password could not be changed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="public">
      <PublicNav />
      <main className="login">
        <Globe className="login__globe" interactive={false} showLabel={false} />
        <div className="login__intro" />
        <section className="auth-card" aria-labelledby="reset-title">
          <h2 id="reset-title">Set a new password</h2>

          {loading ? (
            <LoadingState compact label="Checking the reset link" />
          ) : !session ? (
            <>
              <div className="notice notice--warn" role="alert" style={{ marginTop: 16 }}>
                <Icon name="alert" size={16} />
                <p>
                  This reset link is no longer valid. Request a new one from the sign-in page.
                </p>
              </div>
              <p className="auth-card__foot">
                <Link to="/login">Back to sign in</Link>
              </p>
            </>
          ) : done ? (
            <div className="notice notice--ok" role="status" style={{ marginTop: 16 }}>
              <Icon name="check" size={16} />
              <p>Password changed. Opening your dashboard.</p>
            </div>
          ) : (
            <form onSubmit={handleSubmit} noValidate>
              <div className="field">
                <label htmlFor={passwordId}>New password</label>
                <div className="input-wrap">
                  <input
                    id={passwordId}
                    className="input"
                    type={show ? "text" : "password"}
                    autoComplete="new-password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    autoFocus
                  />
                  <button
                    type="button"
                    className="input-wrap__action"
                    onClick={() => setShow((value) => !value)}
                    aria-label={show ? "Hide password" : "Show password"}
                    aria-pressed={show}
                  >
                    <Icon name={show ? "eye-off" : "eye"} size={18} />
                  </button>
                </div>
                <span className="field__hint">At least 8 characters.</span>
              </div>
              <div className="field">
                <label htmlFor={confirmId}>Repeat the new password</label>
                <input
                  id={confirmId}
                  className="input"
                  type={show ? "text" : "password"}
                  autoComplete="new-password"
                  value={confirm}
                  onChange={(event) => setConfirm(event.target.value)}
                />
              </div>
              {error ? (
                <div className="notice notice--error" role="alert">
                  <Icon name="alert" size={16} />
                  <p>{error}</p>
                </div>
              ) : null}
              <button type="submit" className="btn btn--primary btn--block" disabled={busy}>
                {busy ? "Saving" : "Change password"}
              </button>
            </form>
          )}
        </section>
      </main>
    </div>
  );
}
