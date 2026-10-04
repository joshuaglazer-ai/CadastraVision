import { useEffect, useId, useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import Globe from "../components/Globe";
import GoogleSignIn from "../components/GoogleSignIn";
import Icon from "../components/Icon";
import PublicNav from "../components/PublicNav";
import { useAuth } from "../context/AuthContext";
import { PRODUCT } from "../lib/constants";

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function authMessage(error) {
  const message = String(error?.message || "");
  if (/invalid login credentials/i.test(message)) {
    return "That e-mail and password do not match an account. Check both and try again.";
  }
  if (/email not confirmed/i.test(message)) {
    return "This e-mail address has not been confirmed yet. Open the confirmation link sent to it.";
  }
  if (/failed to fetch|network/i.test(message)) {
    return "The sign-in service could not be reached. Check your connection and try again.";
  }
  return message || "Sign-in failed. Try again.";
}

export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const { authenticated, loading, configured, mode, signIn, requestPasswordReset } = useAuth();

  const [view, setView] = useState("signin"); // signin | forgot
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState({});
  const [sent, setSent] = useState("");

  const emailId = useId();
  const passwordId = useId();
  const errorId = useId();

  const destination = location.state?.from?.pathname || "/dashboard";

  useEffect(() => {
    setError("");
    setFieldErrors({});
    setSent("");
  }, [view]);

  if (!loading && authenticated) {
    return <Navigate to={destination} replace />;
  }

  function validate(requirePassword) {
    const next = {};
    if (!email.trim()) next.email = "Enter your e-mail address.";
    else if (!EMAIL.test(email.trim())) next.email = "Enter a valid e-mail address, like name@agency.gov.in.";
    if (requirePassword && !password) next.password = "Enter your password.";
    setFieldErrors(next);
    return Object.keys(next).length === 0;
  }

  async function handleSignIn(event) {
    event.preventDefault();
    if (!validate(true)) return;
    setBusy(true);
    setError("");
    try {
      await signIn(email.trim(), password);
      navigate(destination, { replace: true });
    } catch (err) {
      setError(authMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleForgot(event) {
    event.preventDefault();
    if (!validate(false)) return;
    setBusy(true);
    setError("");
    try {
      await requestPasswordReset(email.trim());
      setSent(`If an account exists for ${email.trim()}, a link to set a new password is on its way.`);
    } catch (err) {
      setError(authMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="public">
      <PublicNav />
      <main className="login">
        <Globe className="login__globe" showLabel={false} />

        <div className="login__intro fade-in">
          <p className="login__brand">
            <strong>{PRODUCT.name}</strong>
            <span>{PRODUCT.tagline}</span>
          </p>
          <h1>AI draws the first draft. Surveyors have the last word.</h1>
          <p>
            {PRODUCT.name} turns a drone image into measured buildings, roads, fields and candidate
            plots, checks the geometry, and shows the surveyor where to look first.
          </p>
          <ul className="login__points">
            <li>
              <strong>Traced for you.</strong> Buildings, roads and fields are drawn from the drone
              image in minutes.
            </li>
            <li>
              <strong>Checked before you see it.</strong> Every shape is measured in metres, repaired
              if broken, and carries the model&apos;s confidence.
            </li>
            <li>
              <strong>Yours to decide.</strong> Approve, correct or reject each shape. Every decision
              is recorded.
            </li>
          </ul>
          <p className="login__motto">AI proposes. GIS validates. Surveyors verify.</p>
        </div>

        <section className="auth-card fade-in" aria-labelledby="auth-title">
          {view === "signin" ? (
            <>
              <h2 id="auth-title">Sign in</h2>
              <p className="auth-card__sub">Use your surveyor account.</p>

              {!configured ? (
                <div className="notice notice--error" role="alert" style={{ marginTop: 16 }}>
                  <Icon name="alert" size={16} />
                  <p>
                    Sign-in is not configured for this installation. Set VITE_SUPABASE_URL and
                    VITE_SUPABASE_ANON_KEY in frontend/.env, then restart the frontend.
                  </p>
                </div>
              ) : null}

              <form onSubmit={handleSignIn} noValidate>
                <div className="field">
                  <label htmlFor={emailId}>E-mail</label>
                  <input
                    id={emailId}
                    className="input"
                    type="email"
                    autoComplete="username"
                    inputMode="email"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    placeholder="name@agency.gov.in"
                    aria-invalid={Boolean(fieldErrors.email)}
                    aria-describedby={fieldErrors.email ? `${emailId}-error` : undefined}
                    autoFocus
                  />
                  {fieldErrors.email ? (
                    <span className="field__error" id={`${emailId}-error`}>
                      {fieldErrors.email}
                    </span>
                  ) : null}
                </div>

                <div className="field">
                  <div className="row row--between">
                    <label htmlFor={passwordId} style={{ fontSize: "0.86rem", fontWeight: 500, color: "var(--mist)" }}>
                      Password
                    </label>
                    <button type="button" className="link-btn" onClick={() => setView("forgot")}>
                      Forgot password?
                    </button>
                  </div>
                  <div className="input-wrap">
                    <input
                      id={passwordId}
                      className="input"
                      type={showPassword ? "text" : "password"}
                      autoComplete="current-password"
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      aria-invalid={Boolean(fieldErrors.password)}
                      aria-describedby={fieldErrors.password ? `${passwordId}-error` : undefined}
                    />
                    <button
                      type="button"
                      className="input-wrap__action"
                      onClick={() => setShowPassword((value) => !value)}
                      aria-label={showPassword ? "Hide password" : "Show password"}
                      aria-pressed={showPassword}
                    >
                      <Icon name={showPassword ? "eye-off" : "eye"} size={18} />
                    </button>
                  </div>
                  {fieldErrors.password ? (
                    <span className="field__error" id={`${passwordId}-error`}>
                      {fieldErrors.password}
                    </span>
                  ) : null}
                </div>

                {error ? (
                  <div className="notice notice--error" role="alert" id={errorId}>
                    <Icon name="alert" size={16} />
                    <p>{error}</p>
                  </div>
                ) : null}

                <button type="submit" className="btn btn--primary btn--block" disabled={busy || !configured}>
                  {busy ? "Signing in" : "Sign in"}
                </button>
              </form>

              <GoogleSignIn disabled={!configured || mode === "off"} />

              <p className="auth-card__switch">
                New surveyor? <Link to="/signup">Create an account</Link>
              </p>

              <p className="auth-card__foot">
                Your session stays signed in on this device until you sign out. {PRODUCT.disclaimer}
              </p>
            </>
          ) : (
            <>
              <h2 id="auth-title">Reset your password</h2>
              <p className="auth-card__sub">
                Enter the e-mail address of your account. A link to set a new password will be sent to it.
              </p>

              <form onSubmit={handleForgot} noValidate>
                <div className="field">
                  <label htmlFor={emailId}>E-mail</label>
                  <input
                    id={emailId}
                    className="input"
                    type="email"
                    autoComplete="username"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    placeholder="name@agency.gov.in"
                    aria-invalid={Boolean(fieldErrors.email)}
                    autoFocus
                  />
                  {fieldErrors.email ? <span className="field__error">{fieldErrors.email}</span> : null}
                </div>

                {sent ? (
                  <div className="notice notice--ok" role="status">
                    <Icon name="mail" size={16} />
                    <p>{sent}</p>
                  </div>
                ) : null}
                {error ? (
                  <div className="notice notice--error" role="alert">
                    <Icon name="alert" size={16} />
                    <p>{error}</p>
                  </div>
                ) : null}

                <button type="submit" className="btn btn--primary btn--block" disabled={busy || !configured}>
                  {busy ? "Sending" : "Send reset link"}
                </button>
                <button type="button" className="btn btn--ghost btn--block" onClick={() => setView("signin")}>
                  <Icon name="arrow-left" size={16} /> Back to sign in
                </button>
              </form>
            </>
          )}

          {mode === "off" ? (
            <p className="auth-card__foot">
              Development mode is on: <Link to="/dashboard">open the dashboard</Link> without signing in.
            </p>
          ) : null}
        </section>
      </main>
    </div>
  );
}
