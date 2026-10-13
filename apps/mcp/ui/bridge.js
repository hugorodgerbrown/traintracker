/*
 * apps/mcp/ui/bridge.js — the view's side of MCP Apps, inlined into every view.
 *
 * The client draws a view in a sandboxed iframe and talks to it with
 * JSON-RPC over postMessage (the MCP Apps spec, 2026-01-26). This file does
 * the handshake, applies the host's theme and style variables, reports the
 * view's height so the frame fits it, and hands the tool result to the view:
 *
 *   McpApp.onToolResult((result) => draw(result.structuredContent));
 *   McpApp.callTool('add_note', { text: 'hi' });   // a promise of its result
 *
 * The SDK's App class does the same with a build step; the template has none.
 */
(function () {
  'use strict';

  const PROTOCOL_VERSION = '2026-01-26';
  const pending = new Map();
  const listeners = { 'tool-input': [], 'tool-result': [] };
  const latest = {};
  let nextId = 1;

  // The host's origin isn't known to a sandboxed view, so '*' is the spec's
  // own choice; nothing secret is sent, and replies are checked by source.
  function send(message) {
    // nosemgrep: javascript.browser.security.wildcard-postmessage-configuration.wildcard-postmessage-configuration
    window.parent.postMessage(Object.assign({ jsonrpc: '2.0' }, message), '*');
  }

  function request(method, params) {
    const id = nextId++;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      send({ id, method, params });
    });
  }

  function applyContext(context) {
    if (!context) return;
    const root = document.documentElement;
    if (context.theme === 'light' || context.theme === 'dark') {
      root.dataset.theme = context.theme;
      root.style.colorScheme = context.theme;
    }
    const variables = (context.styles && context.styles.variables) || {};
    for (const [name, value] of Object.entries(variables)) {
      if (name.startsWith('--') && typeof value === 'string') root.style.setProperty(name, value);
    }
  }

  function emit(kind, params) {
    latest[kind] = params;
    for (const listener of listeners[kind]) listener(params);
  }

  function on(kind) {
    return (listener) => {
      listeners[kind].push(listener);
      if (kind in latest) listener(latest[kind]);
    };
  }

  let lastHeight = -1;
  function reportSize() {
    const height = Math.ceil(document.documentElement.getBoundingClientRect().height);
    if (height === lastHeight) return;
    lastHeight = height;
    send({ method: 'ui/notifications/size-changed', params: { width: Math.ceil(window.innerWidth), height } });
  }

  // Only the host frame speaks to the view: checked by source, as its origin is unknown.
  // nosemgrep: javascript.browser.security.insufficient-postmessage-origin-validation.insufficient-postmessage-origin-validation
  window.addEventListener('message', (event) => {
    if (event.source !== window.parent) return;
    const message = event.data;
    if (!message || message.jsonrpc !== '2.0') return;

    if (!('method' in message)) {
      const waiting = pending.get(message.id);
      if (!waiting) return;
      pending.delete(message.id);
      if (message.error) waiting.reject(new Error(message.error.message));
      else waiting.resolve(message.result);
      return;
    }

    switch (message.method) {
      case 'ui/notifications/tool-input':
        emit('tool-input', (message.params && message.params.arguments) || {});
        break;
      case 'ui/notifications/tool-result':
        emit('tool-result', message.params || {});
        break;
      case 'ui/notifications/host-context-changed':
        applyContext(message.params);
        break;
    }
    // A request from the host (teardown, ping) wants an answer; none needs work here.
    if ('id' in message) send({ id: message.id, result: {} });
  });

  const ready = request('ui/initialize', {
    protocolVersion: PROTOCOL_VERSION,
    appInfo: { name: document.title || 'view', version: '1.0.0' },
    appCapabilities: {},
  }).then((result) => {
    applyContext(result && result.hostContext);
    send({ method: 'ui/notifications/initialized' });
    reportSize();
    if (typeof ResizeObserver === 'function') new ResizeObserver(reportSize).observe(document.body);
    return result;
  });

  self.McpApp = {
    ready,
    onToolInput: on('tool-input'),
    onToolResult: on('tool-result'),
    callTool: (name, args) => request('tools/call', { name, arguments: args || {} }),
  };
})();
