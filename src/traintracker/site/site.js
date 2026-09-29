// Copy buttons. They start hidden, so without JavaScript the page shows the
// URL as text to select, and no button that does nothing.
for (const button of document.querySelectorAll("button[data-copy]")) {
  const source = document.querySelector(button.dataset.copy);
  const status = button.parentElement.querySelector("[role=status]");
  if (!source || !navigator.clipboard) continue;
  button.hidden = false;
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(source.textContent.trim());
      status.textContent = "Copied.";
    } catch {
      status.textContent = "Couldn't copy. Select the address and copy it by hand.";
    }
  });
}
