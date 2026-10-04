import { useId, useRef, useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import Globe from "../components/Globe";
import GoogleSignIn from "../components/GoogleSignIn";
import Icon from "../components/Icon";
import PublicNav from "../components/PublicNav";
import { useAuth } from "../context/AuthContext";
import { PRODUCT } from "../lib/constants";
import { MIN_PASSWORD_LENGTH, signUpMessage, validateSignup } from "../lib/signup";

const FIELD_ORDER = ["name", "govtId", "email", "password", "confirm"];

function FieldError({ id, message }) {
  if (!message) return null;
  return (
    <span className="field__error" id={id}>
      {message}
    </span>
  );
}

export default function Signup() {
  const navigate = useNavigate();
  const { authenticated, loading, configured, mode, signUp } = useAuth();

  const [name, setName] = useState("");
  const [govtId, setGovtId] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState({});
  const [confirmationSentTo, setConfirmationSentTo] = useState("");

  const ids = {
    name: useId(),
    govtId: useId(),
    email: useId(),
    password: useId(),
    confirm: useId(),
  };
  const refs = {
    name: useRef(null),
    govtId: useRef(null),
    email: useRef(null),
    password: useRef(null),
    confirm: useRef(null),
  };

  // Account creation needs a real Supabase project. In development mode or
  // without configuration it is refused rather than pretended.
  const unavailableReason =
    mode === "off"
      ? "Account creation is unavailable in development mode (VITE_AUTH_MODE=off): there is no sign-in service to create the account in."
      : !configured
        ? "Account creation is unavailable because sign-in is not configured for this installation. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY in frontend/.env, then restart the frontend."
        : "";

  if (!loading && authenticated && mode !== "off") {
    return <Navigate to="/dashboard" replace />;
  }

  function describedBy(key, hint) {
    const parts = [];
    if (hint) parts.push(`${ids[key]}-hint`);
    if (fieldErrors[key]) parts.push(`${ids[key]}-error`);
    return parts.length ? parts.join(" ") : undefined;
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (unavailableReason) return;
    const next = validateSignup({ name, govtId, email, password, confirm });
    setFieldErrors(next);
    const firstInvalid = FIELD_ORDER.find((key) => next[key]);
    if (firstInvalid) {
      refs[firstInvalid].current?.focus();
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await signUp(name.trim(), email.trim(), password, govtId.trim());
      if (result.session) {
        navigate("/dashboard", { replace: true });
      } else {
        setConfirmationSentTo(email.trim());
        setPassword("");
        setConfirm("");
      }
    } catch (err) {
      setError(signUpMessage(err));
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
          <h1>Join the survey desk.</h1>
          <p>
            Create a surveyor account, then add the area you are working in, or work in one an administrator
            has assigned to you.
          </p>
          <div className="principle" aria-label="Operating principle">
            <span>AI proposes</span>
            <span>GIS validates</span>
            <span>Surveyors verify</span>
          </div>
        </div>

        <section className="auth-card fade-in" aria-labelledby="signup-title">
          {confirmationSentTo ? (
            <>
              <h2 id="signup-title">Check your e-mail to confirm your account</h2>
              <div className="notice notice--ok" role="status" style={{ marginTop: 16 }}>
                <Icon name="mail" size={16} />
                <p>
                  A confirmation link has been sent to <strong>{confirmationSentTo}</strong>. Open it, then
                  sign in with your new password.
                </p>
              </div>
              <p className="auth-card__sub" style={{ marginTop: 14 }}>
                No e-mail after a few minutes? Check the spam folder, or try signing in: an address that was
                already registered does not receive a second confirmation.
              </p>
              <Link to="/login" className="btn btn--primary btn--block" style={{ marginTop: 18 }}>
                Go to sign in
              </Link>
            </>
          ) : (
            <>
              <h2 id="signup-title">Create an account</h2>
              <p className="auth-card__sub">For surveyors. You will need your government surveyor ID.</p>

              {unavailableReason ? (
                <div className="notice notice--error" role="alert" style={{ marginTop: 16 }}>
                  <Icon name="alert" size={16} />
                  <p>{unavailableReason}</p>
                </div>
              ) : null}

              <form onSubmit={handleSubmit} noValidate>
                <div className="field">
                  <label htmlFor={ids.name}>Full name</label>
                  <input
                    ref={refs.name}
                    id={ids.name}
                    className="input"
                    type="text"
                    autoComplete="name"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    placeholder="Name as it should appear on reviews"
                    aria-invalid={Boolean(fieldErrors.name)}
                    aria-describedby={describedBy("name")}
                    autoFocus
                  />
                  <FieldError id={`${ids.name}-error`} message={fieldErrors.name} />
                </div>

                <div className="field">
                  <label htmlFor={ids.govtId}>Government surveyor ID</label>
                  <input
                    ref={refs.govtId}
                    id={ids.govtId}
                    className="input"
                    type="text"
                    autoComplete="off"
                    value={govtId}
                    onChange={(event) => setGovtId(event.target.value)}
                    placeholder="As printed on your departmental ID"
                    aria-invalid={Boolean(fieldErrors.govtId)}
                    aria-describedby={describedBy("govtId", true)}
                  />
                  <span className="field__hint" id={`${ids.govtId}-hint`}>
                    Self-declared: not checked against a government register.
                  </span>
                  <FieldError id={`${ids.govtId}-error`} message={fieldErrors.govtId} />
                </div>

                <div className="field">
                  <label htmlFor={ids.email}>E-mail</label>
                  <input
                    ref={refs.email}
                    id={ids.email}
                    className="input"
                    type="email"
                    autoComplete="email"
                    inputMode="email"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    placeholder="name@agency.gov.in"
                    aria-invalid={Boolean(fieldErrors.email)}
                    aria-describedby={describedBy("email")}
                  />
                  <FieldError id={`${ids.email}-error`} message={fieldErrors.email} />
                </div>

                <div className="field">
                  <label htmlFor={ids.password}>Password</label>
                  <div className="input-wrap">
                    <input
                      ref={refs.password}
                      id={ids.password}
                      className="input"
                      type={showPassword ? "text" : "password"}
                      autoComplete="new-password"
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      aria-invalid={Boolean(fieldErrors.password)}
                      aria-describedby={describedBy("password", true)}
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
                  <span className="field__hint" id={`${ids.password}-hint`}>
                    At least {MIN_PASSWORD_LENGTH} characters.
                  </span>
                  <FieldError id={`${ids.password}-error`} message={fieldErrors.password} />
                </div>

                <div className="field">
                  <label htmlFor={ids.confirm}>Confirm password</label>
                  <input
                    ref={refs.confirm}
                    id={ids.confirm}
                    className="input"
                    type={showPassword ? "text" : "password"}
                    autoComplete="new-password"
                    value={confirm}
                    onChange={(event) => setConfirm(event.target.value)}
                    aria-invalid={Boolean(fieldErrors.confirm)}
                    aria-describedby={describedBy("confirm")}
                  />
                  <FieldError id={`${ids.confirm}-error`} message={fieldErrors.confirm} />
                </div>

                {error ? (
                  <div className="notice notice--error" role="alert">
                    <Icon name="alert" size={16} />
                    <p>{error}</p>
                  </div>
                ) : null}

                <button
                  type="submit"
                  className="btn btn--primary btn--block"
                  disabled={busy || Boolean(unavailableReason)}
                >
                  {busy ? "Creating account" : "Create account"}
                </button>
              </form>

              <GoogleSignIn disabled={Boolean(unavailableReason)} />

              <p className="auth-card__foot">
                Already have an account? <Link to="/login">Sign in</Link>. {PRODUCT.disclaimer}
              </p>
            </>
          )}
        </section>
      </main>
    </div>
  );
}
