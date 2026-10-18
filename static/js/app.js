// Bayani Bank — shared helpers (CSRF fetch, toasts, masking, idle sign-out)
(() => {
  const meta = document.querySelector('meta[name="csrf-token"]');

  const BB = (window.BB = {
    csrf: () => meta.content,
    setCsrf(token) {
      meta.content = token;
      document.querySelectorAll('input[name="csrf_token"]').forEach((i) => (i.value = token));
    },

    async post(url, body = {}) {
      let res, data = {};
      try {
        res = await fetch(url, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': BB.csrf() },
          body: JSON.stringify(body),
        });
        data = await res.json().catch(() => ({}));
      } catch (e) {
        return { ok: false, status: 0, data: { error: 'Network problem. Check your connection and try again.' } };
      }
      if (data.csrf) BB.setCsrf(data.csrf);
      if (data.redirect && [401, 403, 423].includes(res.status)) {
        BB.toast(data.error || 'Redirecting…', 'error');
        setTimeout(() => (location.href = data.redirect), 1800);
      }
      return { ok: res.ok, status: res.status, data };
    },

    toast(message, type = 'info') {
      const box = document.getElementById('toasts');
      if (!box) return;
      const icons = { success: 'circle-check', error: 'circle-alert', info: 'info' };
      const el = document.createElement('div');
      el.className = `toast ${type}`;
      el.setAttribute('role', type === 'error' ? 'alert' : 'status');
      const icon = document.createElement('i');
      icon.setAttribute('data-lucide', icons[type] || 'info');
      icon.className = 'w-5 h-5 shrink-0';
      const text = document.createElement('p');
      text.className = 'flex-1';
      text.textContent = message;
      el.append(icon, text);
      box.appendChild(el);
      BB.icons();
      setTimeout(() => {
        el.style.transition = 'opacity .3s';
        el.style.opacity = '0';
        setTimeout(() => el.remove(), 300);
      }, type === 'error' ? 6500 : 4500);
    },

    icons() {
      if (window.lucide) window.lucide.createIcons();
    },

    // Show/hide masked values inside a [data-mask-group]
    setMasked(group, masked) {
      group.querySelectorAll('[data-masked]').forEach((e) => e.classList.toggle('hidden', !masked));
      group.querySelectorAll('[data-unmasked]').forEach((e) => e.classList.toggle('hidden', masked));
      group.querySelectorAll('[data-toggle-label]').forEach((e) => (e.textContent = masked ? 'Show' : 'Hide'));
      group.dataset.revealed = masked ? '' : '1';
      clearTimeout(group._remask);
      if (!masked) group._remask = setTimeout(() => BB.setMasked(group, true), 15000); // auto re-hide
    },
    maskAll() {
      document.querySelectorAll('[data-mask-group]').forEach((g) => BB.setMasked(g, true));
    },
  });

  document.addEventListener('click', (e) => {
    const toggle = e.target.closest('[data-toggle-mask]');
    if (toggle) {
      const group = toggle.closest('[data-mask-group]');
      BB.setMasked(group, group.dataset.revealed === '1');
    }
    const reveal = e.target.closest('[data-reveal]');
    if (reveal) {
      const input = reveal.parentElement.querySelector('input');
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      reveal.innerHTML = `<i data-lucide="${show ? 'eye-off' : 'eye'}" class="w-5 h-5"></i>`;
      BB.icons();
    }
  });

  // Digits only for PIN fields
  document.addEventListener('input', (e) => {
    if (e.target.matches('[data-pin]')) e.target.value = e.target.value.replace(/\D/g, '').slice(0, 6);
  });

  // Confirm dangerous admin actions
  document.addEventListener('submit', (e) => {
    const msg = e.target.dataset.confirm;
    if (msg && !window.confirm(msg)) e.preventDefault();
  });

  // Flash messages -> toasts
  const flashData = document.getElementById('flash-data');
  if (flashData) JSON.parse(flashData.textContent).forEach(([cat, msg]) => BB.toast(msg, cat === 'message' ? 'info' : cat));

  // Idle sign-out: warn at 60s left, sign out at 0. Activity pings keep the server session alive.
  const idleSeconds = parseInt(document.body.dataset.idleSeconds || '0', 10);
  if (idleSeconds > 0) {
    const warnAt = 60;
    const warning = document.getElementById('idle-warning');
    const countEl = document.getElementById('idle-count');
    let last = Date.now();
    let lastPing = Date.now();

    const activity = () => {
      if (!warning.classList.contains('hidden')) return; // must press "Stay" once warned
      last = Date.now();
      if (Date.now() - lastPing > 60000) {
        lastPing = Date.now();
        BB.post(document.body.dataset.pingUrl);
      }
    };
    ['mousemove', 'keydown', 'click', 'scroll', 'touchstart'].forEach((ev) =>
      window.addEventListener(ev, activity, { passive: true })
    );
    document.getElementById('idle-stay').addEventListener('click', () => {
      warning.classList.add('hidden');
      last = lastPing = Date.now();
      BB.post(document.body.dataset.pingUrl);
    });
    setInterval(() => {
      const left = idleSeconds - Math.floor((Date.now() - last) / 1000);
      if (left <= 0) {
        location.href = document.body.dataset.logoutUrl;
      } else if (left <= warnAt) {
        warning.classList.remove('hidden');
        countEl.textContent = left;
      }
    }, 1000);
  }

  BB.icons();
})();
