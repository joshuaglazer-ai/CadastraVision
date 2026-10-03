// Run with: npm test   (Node's built-in test runner, no extra packages)
import assert from "node:assert/strict";
import test from "node:test";

import { MIN_PASSWORD_LENGTH, oauthMessage, signUpMessage, validateGovtId, validateSignup } from "./signup.js";

const valid = {
  name: "Asha Rao",
  govtId: "UP/SRV/0412",
  email: "asha@agency.gov.in",
  password: "survey-2026",
  confirm: "survey-2026",
};

test("a complete form has no errors", () => {
  assert.deepEqual(validateSignup(valid), {});
});

test("every field is required", () => {
  assert.deepEqual(Object.keys(validateSignup({})).sort(), ["confirm", "email", "govtId", "name", "password"]);
  assert.ok(validateSignup({ ...valid, name: "   " }).name);
});

test("the e-mail address must look like one", () => {
  for (const email of ["asha", "asha@", "asha@agency", "a b@agency.gov.in"]) {
    assert.ok(validateSignup({ ...valid, email }).email, email);
  }
});

test(`the password needs at least ${MIN_PASSWORD_LENGTH} characters`, () => {
  const short = "a".repeat(MIN_PASSWORD_LENGTH - 1);
  assert.match(validateSignup({ ...valid, password: short, confirm: short }).password, /at least 8/);
  const exact = "a".repeat(MIN_PASSWORD_LENGTH);
  assert.equal(validateSignup({ ...valid, password: exact, confirm: exact }).password, undefined);
});

test("the two passwords must match", () => {
  assert.match(validateSignup({ ...valid, confirm: "something-else" }).confirm, /do not match/);
});

test("Supabase errors are turned into plain messages", () => {
  const cases = [
    [{ code: "user_already_exists", message: "User already registered" }, /already exists/],
    [{ message: "User already registered" }, /already exists/],
    [{ code: "weak_password", message: "Password should contain..." }, /too weak/],
    [{ code: "signup_disabled", message: "Signups not allowed for this instance" }, /sign-up is switched off/],
    [{ message: "Email signups are disabled" }, /sign-up is switched off/],
    [{ name: "AuthRetryableFetchError", message: "Failed to fetch" }, /could not be reached/],
    [{ code: "over_email_send_rate_limit", message: "email rate limit exceeded" }, /Too many attempts/],
  ];
  for (const [error, expected] of cases) {
    assert.match(signUpMessage(error), expected, JSON.stringify(error));
  }
  assert.equal(signUpMessage({ message: "Something new" }), "Something new");
  assert.match(signUpMessage(null), /could not be created/);
});

test("a government surveyor ID is required and has a sane shape", () => {
  for (const id of ["UP/SRV/0412", "SRV-0412", "KA 12.334_7", "abc"]) {
    assert.equal(validateGovtId(id), "", id);
  }
  assert.match(validateGovtId(""), /Enter your government surveyor ID/);
  for (const id of ["ab", "-SRV1", "SRV1/", "SRV#1", "x".repeat(41)]) {
    assert.match(validateGovtId(id), /3 to 40/, id);
  }
});

test("Google sign-in errors are explained", () => {
  assert.match(oauthMessage({ message: "Unsupported provider: provider is not enabled" }), /not enabled/);
  assert.match(oauthMessage({ name: "AuthRetryableFetchError", message: "Failed to fetch" }), /could not be reached/);
  assert.match(oauthMessage(null), /could not be started/);
});
