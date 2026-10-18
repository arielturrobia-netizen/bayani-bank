// Bayani Bank — Hero HQ admin console: tabs, customer search, one-time PIN reveal
(() => {
  const tabs = document.querySelectorAll('.admin-tab');
  tabs.forEach((tab) =>
    tab.addEventListener('click', () => {
      tabs.forEach((t) => t.classList.toggle('is-active', t === tab));
      document.querySelectorAll('[data-panel]').forEach((p) => p.classList.toggle('hidden', p.dataset.panel !== tab.dataset.tab));
      try { sessionStorage.setItem('bayani-hq-tab', tab.dataset.tab); } catch (e) {}
    })
  );
  try {
    const saved = sessionStorage.getItem('bayani-hq-tab');
    if (saved) document.querySelector(`.admin-tab[data-tab="${saved}"]`)?.click();
  } catch (e) {}

  const search = document.getElementById('customer-search');
  if (search) {
    search.addEventListener('input', () => {
      const q = search.value.toLowerCase().replace(/\s+/g, ' ').trim();
      const qDigits = q.replace(/\s/g, '');
      let shown = 0;
      document.querySelectorAll('.customer-row').forEach((row) => {
        const hay = row.dataset.search;
        const match = !q || hay.includes(q) || (qDigits && /^\d+$/.test(qDigits) && hay.includes(qDigits));
        row.classList.toggle('hidden', !match);
        if (match) shown++;
      });
      document.getElementById('no-match').classList.toggle('hidden', shown > 0 || !q);
    });
  }

  const modal = document.getElementById('reveal-modal');
  if (modal) {
    document.getElementById('reveal-done').addEventListener('click', () => modal.remove());
    const copyBtn = document.getElementById('copy-details');
    copyBtn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(copyBtn.dataset.copy);
        BB.toast('Account number and PIN copied.', 'success');
      } catch (e) {
        BB.toast('Copy failed. Please write them down instead.', 'error');
      }
    });
  }
})();
