// Vitest: the JavaScript unit tests in tests/js (see docs/testing.md).
// jsdom plays the page, with its own localStorage.
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'jsdom',
    include: ['tests/js/**/test_*.js'],
  },
});
