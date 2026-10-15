/*
 * static/js/passkeys.js — passkey sign-in (sign-in page) and adding one (account page).
 *
 * Both pages render the passkey controls hidden; this file shows them only
 * where the browser can make or use passkeys, so a browser without WebAuthn
 * sees the email form alone. The server keeps the challenge in the session
 * (apps/accounts/passkeys.py); every request here is same-origin with the
 * CSRF token.
 *
 * Nothing asks for a passkey until the button is pressed: no autofill
 * (conditional mediation) request on page load, which some browsers and
 * password managers show as a prompt before anyone has asked for one.
 */
(function () {
  'use strict';

  const codec = self.WebAuthnJSON;
  const supported = Boolean(self.PublicKeyCredential && navigator.credentials && codec);

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content) return meta.content;
    const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return input ? input.value : '';
  }

  async function postJSON(url, body) {
    const response = await fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
      body: body === undefined ? '{}' : JSON.stringify(body),
    });
    let data = {};
    try {
      data = await response.json();
    } catch (error) {
      data = {};
    }
    return { status: response.status, ok: response.ok, data: data };
  }

  function showError(section, message) {
    const el = section.querySelector('[data-passkey-error]');
    if (!el) return;
    el.textContent = message;
    el.hidden = !message;
  }

  /** What to tell the user when a ceremony fails. Empty means say nothing. */
  function describe(error) {
    if (!navigator.onLine) return "You're offline. Try again when you're back.";
    if (error && error.name === 'NotAllowedError') return ''; // cancelled, or timed out
    if (error && error.name === 'AbortError') return '';
    if (error && error.name === 'InvalidStateError') return 'This device already has a passkey for your account.';
    return "That didn't work. Try again, or use your email.";
  }

  // ---------- sign in ----------

  async function signIn(section) {
    const options = await postJSON(section.dataset.optionsUrl);
    if (!options.ok) throw new Error('options ' + options.status);
    const credential = await navigator.credentials.get({ publicKey: codec.requestOptions(options.data) });
    if (!credential) return;
    const result = await postJSON(section.dataset.verifyUrl, {
      credential: codec.credentialToJSON(credential),
      next: section.dataset.next || '',
    });
    if (result.ok) {
      location.assign(result.data.next);
      return;
    }
    if (result.status === 404 && result.data.credentialId) {
      // Deleted here but still on the device: ask the device to forget it,
      // under the RP ID the ceremony used (it can be a parent domain).
      const PKC = self.PublicKeyCredential;
      if (typeof PKC.signalUnknownCredential === 'function') {
        PKC.signalUnknownCredential({ rpId: options.data.rpId || location.hostname, credentialId: result.data.credentialId }).catch(() => undefined);
      }
      showError(section, "That passkey isn't on your account any more. Sign in with your email.");
      return;
    }
    showError(section, describe(null));
  }

  function setUpSignIn(section) {
    section.hidden = false;
    const button = section.querySelector('[data-passkey-button]');
    button.addEventListener('click', () => {
      showError(section, '');
      button.disabled = true;
      signIn(section)
        .catch((error) => showError(section, describe(error)))
        .finally(() => {
          button.disabled = false;
        });
    });
  }

  // ---------- add a passkey ----------

  async function register(section) {
    const options = await postJSON(section.dataset.optionsUrl);
    if (!options.ok) throw new Error('options ' + options.status);
    const credential = await navigator.credentials.create({ publicKey: codec.creationOptions(options.data) });
    const result = await postJSON(section.dataset.registerUrl, codec.credentialToJSON(credential));
    if (!result.ok) throw new Error('register ' + result.status);
    location.reload();
  }

  function setUpRegister(section) {
    section.querySelector('[data-passkey-controls]').hidden = false;
    const unsupported = section.querySelector('[data-passkey-unsupported]');
    if (unsupported) unsupported.hidden = true;
    const button = section.querySelector('[data-passkey-button]');
    button.addEventListener('click', () => {
      showError(section, '');
      button.disabled = true;
      register(section)
        .catch((error) => showError(section, describe(error)))
        .finally(() => {
          button.disabled = false;
        });
    });
  }

  if (!supported) return;
  const signInSection = document.querySelector('[data-passkey-sign-in]');
  if (signInSection) setUpSignIn(signInSection);
  const registerSection = document.querySelector('[data-passkey-register]');
  if (registerSection) setUpRegister(registerSection);

  self.Passkeys = Object.freeze({ describe });
})();
