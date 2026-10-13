/*
 * static/js/code_input.js — the sign-in code as six boxes, one digit each.
 *
 * Without this file the code is one text field, which works everywhere.
 * With it, that field becomes hidden and six boxes stand in for it: typing
 * a digit moves to the next box, Backspace on an empty box goes back, and
 * the arrow keys move between them. Pasting the code into any box, or
 * iPhone's "From Messages/Mail" autofill (``one-time-code`` on the first
 * box), spreads it across all six. The hidden field always holds what the
 * boxes say, a full code submits the form, and a short one doesn't.
 */
(function () {
  'use strict';

  function digitsOf(text) {
    return String(text || '').replace(/\D/g, '');
  }

  function enhance(input) {
    const form = input.form;
    const length = Number(input.dataset.codeLength) || 6;
    const label =
      (input.id && document.querySelector('label[for="' + input.id + '"]')) || input.closest('label');
    const group = document.createElement('div');
    group.className = 'code-boxes';
    group.setAttribute('role', 'group');

    const boxes = [];
    for (let i = 0; i < length; i++) {
      const box = document.createElement('input');
      box.type = 'text';
      box.inputMode = 'numeric';
      box.className = 'code-box';
      box.autocomplete = i === 0 ? 'one-time-code' : 'off';
      box.required = input.required;
      // The first box takes the field's own label; the rest say where they are.
      if (i > 0) box.setAttribute('aria-label', 'Digit ' + (i + 1) + ' of ' + length);
      box.id = (input.id || 'code') + '-' + (i + 1);
      boxes.push(box);
      group.appendChild(box);
    }

    function sync() {
      const code = boxes.map((box) => box.value).join('');
      input.value = code;
      if (code.length === length && form) {
        if (form.requestSubmit) form.requestSubmit();
        else form.submit();
      }
    }

    // Write ``digits`` into the boxes from ``start`` on; focus the next empty one.
    function fill(start, digits) {
      let at = start;
      for (const digit of digits) {
        if (at >= length) break;
        boxes[at].value = digit;
        at++;
      }
      boxes[Math.min(at, length - 1)].focus();
      sync();
    }

    boxes.forEach((box, index) => {
      box.addEventListener('input', () => {
        const digits = digitsOf(box.value);
        box.value = '';
        if (!digits) {
          sync();
          return;
        }
        // A whole code (autofill, or a paste the browser typed in) fills from
        // the first box; anything shorter fills from here.
        fill(digits.length >= length ? 0 : index, digits);
      });
      box.addEventListener('paste', (event) => {
        const digits = digitsOf(event.clipboardData && event.clipboardData.getData('text'));
        if (!digits) return;
        event.preventDefault();
        fill(digits.length >= length ? 0 : index, digits);
      });
      box.addEventListener('keydown', (event) => {
        if (event.key === 'Backspace' && !box.value && index > 0) {
          event.preventDefault();
          boxes[index - 1].value = '';
          boxes[index - 1].focus();
          sync();
        } else if (event.key === 'ArrowLeft' && index > 0) {
          event.preventDefault();
          boxes[index - 1].focus();
        } else if (event.key === 'ArrowRight' && index < length - 1) {
          event.preventDefault();
          boxes[index + 1].focus();
        }
      });
      box.addEventListener('focus', () => box.select());
    });

    // The hidden field can't stop an empty or half-typed code being sent, so
    // hold the form back and put the person in the first empty box.
    if (form) {
      form.addEventListener('submit', (event) => {
        const empty = boxes.find((box) => !box.value);
        if (!empty) return;
        event.preventDefault();
        empty.focus();
      });
    }

        // Start from whatever the field held (a code typed before this ran).
    digitsOf(input.value)
      .slice(0, length)
      .split('')
      .forEach((digit, i) => {
        boxes[i].value = digit;
      });

    input.type = 'hidden';
    input.removeAttribute('required');
    input.after(group);
    if (label) label.htmlFor = boxes[0].id;
    if (input.autofocus || input.hasAttribute('autofocus')) boxes[0].focus();
    return boxes;
  }

  self.CodeInput = { enhance: enhance, digitsOf: digitsOf };
  if (typeof document !== 'undefined') {
    document.querySelectorAll('input[data-code-input]').forEach(enhance);
  }
})();
