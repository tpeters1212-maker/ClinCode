// Live total of per-stratum quotas on the batch planner.
(function () {
  const inputs = document.querySelectorAll("input.quota");
  const out = document.getElementById("quota-total");
  if (!out) return;
  const sum = () => { out.textContent = [...inputs].reduce((a, i) => a + (parseInt(i.value, 10) || 0), 0); };
  inputs.forEach((i) => i.addEventListener("input", sum));
  sum();
})();
