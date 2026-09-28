// Confirmation prompts for destructive buttons (inline handlers are blocked by CSP).
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-confirm]");
  if (b && !window.confirm(b.dataset.confirm)) e.preventDefault();
});
