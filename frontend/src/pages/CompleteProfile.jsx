import { useId, useRef, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import Globe from "../components/Globe";
import Icon from "../components/Icon";
import PublicNav from "../components/PublicNav";
import { LoadingState } from "../components/States";
import { useAuth } from "../context/AuthContext";
import { validateGovtId } from "../lib/signup";

/**
 * Asked once of every account without a government surveyor ID, typically
 * after the first Google sign-in. The ID is self-declared.
 */
export default function CompleteProfile() {
  const navigate = useNavigate();
  const location = useLocation();
  const { loading, authenticated, profileComplete, user, updateProfile, signOut } = useAuth();

  const [name, setName] = useState(() => user?.user_metadata?.full_name || user?.user_metadata?.name || "");
  const [govtId, setGovtId] = useState("");
  const [fieldErrors, setFieldErrors] = useState({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const nameId = useId();
  const govtIdId = useId();
  const nameRef = useRef(null);
  const govtIdRef = useRef(null);

  const destination = location.state?.from?.pathname || "/dashboard";

  if (loading) {
    return (
      <div className="app-loading">
        <LoadingState label="Checking your session" />
      </div>
    );
  }
  if (!authenticated) return <Navigate to="/login" replace />;
  if (profileComplete) return <Navigate to={destination} replace />;

  async function handleSubmit(event) {
    event.preventDefault();
    const next = {};
    if (!name.trim()) next.name = "Enter your full name.";
    const govtIdError = validateGovtId(govtId);
    if (govtIdError) next.govtId = govtIdError;
    setFieldErrors(next);
    if (next.name) {
      nameRef.current?.focus();
      return;
    }
    if (next.govtId) {
      govtIdRef.current?.focus();
      return;
    }
    setBusy(true);
    setError("");
    try {
      await updateProfile({ name: name.trim(), govtId: govtId.trim() });
      navigate(destination, { replace: true });
    } catch (err) {
      setError(err?.message || "Your profile could not be saved. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="public">
      <PublicNav />
      <main className="login">
        <Globe className="login__globe" interactive={false} />

        <div className="login__intro fade-in">
          <h1>One more step.</h1>
          <p>
            Enter your government surveyor ID. It is shown with your name on the reviews and exports you make.
          </p>
        </div>

        <section className="auth-card fade-in" aria-labelledby="profile-title">
          <h2 id="profile-title">Complete your surveyor profile</h2>
          <p className="auth-card__sub">Signed in as {user?.email}.</p>

          <form onSubmit={handleSubmit} noValidate>
            <div className="field">
              <label htmlFor={nameId}>Full name</label>
              <input
                ref={nameRef}
                id={nameId}
                className="input"
                type="text"
                autoComplete="name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                aria-invalid={Boolean(fieldErrors.name)}
                aria-describedby={fieldErrors.name ? `${nameId}-error` : undefined}
              />
              {fieldErrors.name ? (
                <span className="field__error" id={`${nameId}-error`}>
                  {fieldErrors.name}
                </span>
              ) : null}
            </div>

            <div className="field">
              <label htmlFor={govtIdId}>Government surveyor ID</label>
              <input
                ref={govtIdRef}
                id={govtIdId}
                className="input"
                type="text"
                autoComplete="off"
                value={govtId}
                onChange={(event) => setGovtId(event.target.value)}
                placeholder="As printed on your departmental ID"
                aria-invalid={Boolean(fieldErrors.govtId)}
                aria-describedby={`${govtIdId}-hint${fieldErrors.govtId ? ` ${govtIdId}-error` : ""}`}
                autoFocus
              />
              <span className="field__hint" id={`${govtIdId}-hint`}>
                Self-declared: it is not checked against a government register, and the application labels it so.
              </span>
              {fieldErrors.govtId ? (
                <span className="field__error" id={`${govtIdId}-error`}>
                  {fieldErrors.govtId}
                </span>
              ) : null}
            </div>

            {error ? (
              <div className="notice notice--error" role="alert">
                <Icon name="alert" size={16} />
                <p>{error}</p>
              </div>
            ) : null}

            <button type="submit" className="btn btn--primary btn--block" disabled={busy}>
              {busy ? "Saving" : "Save and continue"}
            </button>
            <button type="button" className="btn btn--ghost btn--block" onClick={signOut} disabled={busy}>
              Sign out
            </button>
          </form>
        </section>
      </main>
    </div>
  );
}
