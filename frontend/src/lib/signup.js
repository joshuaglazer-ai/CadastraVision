// Validation and messages for creating a surveyor account. Kept free of
// React and Supabase so they can be tested with Node's test runner.

export const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
export const MIN_PASSWORD_LENGTH = 8;

// Government surveyor IDs differ between states and departments, so only the
// shape is checked: 3-40 letters, digits, spaces and - / . _ characters.
// Nothing checks the ID against a government register; it is self-declared.
export const GOVT_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9 ./_-]{1,38}[A-Za-z0-9]$/;

/** Error for a government surveyor ID, or "" when it is acceptable. */
export function validateGovtId(value = "") {
  const id = value.trim();
  if (!id) return "Enter your government surveyor ID.";
  if (!GOVT_ID_PATTERN.test(id)) {
    return "Use 3 to 40 letters or digits; spaces and - / . _ are allowed between them.";
  }
  return "";
}

/** Field errors for the sign-up form, keyed by field; empty when valid. */
export function validateSignup({ name = "", govtId = "", email = "", password = "", confirm = "" }) {
  const errors = {};
  if (!name.trim()) errors.name = "Enter your full name.";
  const govtIdError = validateGovtId(govtId);
  if (govtIdError) errors.govtId = govtIdError;
  if (!email.trim()) errors.email = "Enter your e-mail address.";
  else if (!EMAIL_PATTERN.test(email.trim())) {
    errors.email = "Enter a valid e-mail address, like name@agency.gov.in.";
  }
  if (!password) errors.password = "Choose a password.";
  else if (password.length < MIN_PASSWORD_LENGTH) {
    errors.password = `Use at least ${MIN_PASSWORD_LENGTH} characters.`;
  }
  if (!confirm) errors.confirm = "Enter the password again.";
  else if (password && confirm !== password) errors.confirm = "The two passwords do not match.";
  return errors;
}

/** A Supabase sign-up error in words the surveyor can act on. */
export function signUpMessage(error) {
  const code = String(error?.code || "");
  const message = String(error?.message || "");
  if (code === "user_already_exists" || code === "email_exists" || /already (been )?registered/i.test(message)) {
    return "An account with this e-mail address already exists. Sign in instead, or reset its password.";
  }
  if (code === "weak_password" || /password should|weak password|password is too/i.test(message)) {
    return "That password is too weak for the sign-in service. Use a longer password that mixes letters, numbers and symbols.";
  }
  if (code === "signup_disabled" || code === "email_provider_disabled" || /signups? not allowed|signups? (are )?disabled/i.test(message)) {
    return "New accounts cannot be created on this installation: sign-up is switched off in the sign-in service. Ask your administrator to create your account.";
  }
  if (code === "email_address_invalid" || /unable to validate email|invalid format/i.test(message)) {
    return "The sign-in service did not accept this e-mail address. Check it and try again.";
  }
  if (code === "over_email_send_rate_limit" || code === "over_request_rate_limit" || /rate limit/i.test(message)) {
    return "Too many attempts in a short time. Wait a few minutes and try again.";
  }
  if (/failed to fetch|network|fetch failed/i.test(message) || error?.name === "AuthRetryableFetchError") {
    return "The sign-in service could not be reached. Check your connection and try again.";
  }
  return message || "The account could not be created. Try again.";
}

/** A Supabase Google (OAuth) sign-in error in plain words. */
export function oauthMessage(error) {
  const message = String(error?.message || "");
  if (/provider is not enabled|unsupported provider/i.test(message)) {
    return "Google sign-in is not enabled for this installation. Sign in with e-mail, or ask the administrator to enable the Google provider in Supabase.";
  }
  if (/failed to fetch|network|fetch failed/i.test(message) || error?.name === "AuthRetryableFetchError") {
    return "The sign-in service could not be reached. Check your connection and try again.";
  }
  return message || "Google sign-in could not be started. Try again.";
}
