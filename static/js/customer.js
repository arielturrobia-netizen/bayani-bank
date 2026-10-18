// Bayani Bank — customer home: deposit / withdraw with keypad PIN, OTP, receipt and history filters
(() => {
  const root = document.querySelector('[data-dashboard]');
  const modal = document.getElementById('txn-modal');
  const head = document.getElementById('txn-head');
  const peso = new Intl.NumberFormat('en-PH', { style: 'currency', currency: 'PHP' });
  const MAX = parseFloat(root.dataset.max);
  const $ = (id) => document.getElementById(id);

  const steps = {};
  modal.querySelectorAll('[data-step]').forEach((s) => (steps[s.dataset.step] = s));

  const copy = {
    deposit: { title: 'Deposit', sub: 'How much are you depositing?', done: 'Deposit successful!', headCls: 'bg-flag-700' },
    withdrawal: { title: 'Withdraw', sub: 'How much are you withdrawing?', done: 'Withdrawal successful!', headCls: 'bg-hero-600' },
  };

  let kind = 'deposit';
  let amount = 0;
  let current = 'amount';
  let busy = false;
  let otpTimer = null;
  let cooldownTimer = null;
  const entry = { pin: '', otp: '' };

  // ---------------- Steps ----------------
  function showStep(name) {
    current = name;
    Object.entries(steps).forEach(([k, el]) => el.classList.toggle('hidden', k !== name));
    clearErrors();
    if (name === 'amount') setTimeout(() => $('txn-amount').focus(), 50);
  }
  function clearErrors() {
    modal.querySelectorAll('[data-error]').forEach((e) => { e.textContent = ''; e.classList.add('hidden'); });
  }
  function showError(step, msg) {
    const el = steps[step].querySelector('[data-error]');
    el.textContent = msg;
    el.classList.remove('hidden');
    const dots = steps[step].querySelector('.pin-dots');
    if (dots) { dots.classList.remove('shake'); void dots.offsetWidth; dots.classList.add('shake'); }
  }
  function resetEntry(which) {
    entry[which] = '';
    renderDots(which);
  }
  function wipeSecrets() {
    resetEntry('pin');
    resetEntry('otp');
    $('sim-otp').textContent = '------';
  }

  function open(k) {
    kind = k;
    $('txn-title').textContent = copy[k].title;
    $('txn-sub').textContent = copy[k].sub;
    head.className = `flex items-center justify-between px-5 py-3 text-white ${copy[k].headCls}`;
    $('txn-amount').value = '';
    wipeSecrets();
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    document.body.style.overflow = 'hidden';
    showStep('amount');
  }
  async function close() {
    if (current === 'otp') await BB.post(root.dataset.cancelUrl);
    clearInterval(otpTimer);
    wipeSecrets();
    modal.classList.add('hidden');
    modal.classList.remove('flex');
    document.body.style.overflow = '';
  }

  document.querySelectorAll('[data-open-txn]').forEach((b) => b.addEventListener('click', () => open(b.dataset.openTxn)));
  modal.querySelectorAll('[data-close-txn]').forEach((b) => b.addEventListener('click', close));
  modal.addEventListener('click', (e) => { if (e.target === modal && current !== 'receipt') close(); });

  // ---------------- Amount ----------------
  modal.querySelectorAll('[data-quick]').forEach((c) =>
    c.addEventListener('click', () => { $('txn-amount').value = c.dataset.quick; $('txn-amount').focus(); })
  );
  $('txn-amount').addEventListener('input', (e) => {
    let v = e.target.value.replace(/[^\d.]/g, '');
    const parts = v.split('.');
    if (parts.length > 2) v = parts[0] + '.' + parts.slice(1).join('');
    if (parts[1] && parts[1].length > 2) v = parts[0] + '.' + parts[1].slice(0, 2);
    e.target.value = v;
  });
  steps.amount.addEventListener('submit', (e) => {
    e.preventDefault();
    const v = parseFloat($('txn-amount').value);
    if (!v || v <= 0) return showError('amount', 'Enter an amount greater than zero.');
    if (v > MAX) return showError('amount', `The maximum per transaction is ${peso.format(MAX)}.`);
    amount = Math.round(v * 100) / 100;
    $('pin-summary-label').textContent = copy[kind].title;
    $('pin-summary-amount').textContent = peso.format(amount);
    resetEntry('pin');
    showStep('pin');
  });
  steps.pin.querySelector('[data-back]').addEventListener('click', () => { resetEntry('pin'); showStep('amount'); });

  // ---------------- Keypad ----------------
  function renderDots(which) {
    const dots = modal.querySelectorAll(`[data-dots="${which}"] span`);
    dots.forEach((d, i) => d.classList.toggle('filled', i < entry[which].length));
  }
  function press(which, key) {
    if (busy) return;
    if (key === 'back') entry[which] = entry[which].slice(0, -1);
    else if (entry[which].length < 6) entry[which] += key;
    renderDots(which);
    if (entry[which].length === 6) (which === 'pin' ? submitPin : submitOtp)();
  }
  modal.querySelectorAll('[data-keypad]').forEach((pad) =>
    pad.addEventListener('click', (e) => {
      const k = e.target.closest('[data-key]');
      if (k) press(pad.dataset.keypad, k.dataset.key);
    })
  );
  document.addEventListener('keydown', (e) => {
    if (modal.classList.contains('hidden')) return;
    if (e.key === 'Escape') return close();
    if (current !== 'pin' && current !== 'otp') return;
    if (/^\d$/.test(e.key)) press(current, e.key);
    else if (e.key === 'Backspace') press(current, 'back');
  });

  async function submitPin() {
    busy = true;
    const pin = entry.pin;
    const { ok, data } = await BB.post(root.dataset.txnUrl, { kind, amount: amount.toFixed(2), pin });
    busy = false;
    resetEntry('pin'); // never keep the PIN around
    if (data.status === 'otp_required') return startOtp(data);
    if (ok && data.status === 'ok') return showReceipt(data);
    showError('pin', data.error || 'Something went wrong. Please try again.');
  }

  // ---------------- OTP ----------------
  function startOtp(data) {
    $('otp-amount').textContent = data.amount_display;
    $('sim-otp').textContent = data.sim_otp;
    resetEntry('otp');
    showStep('otp');
    BB.toast(`Simulated SMS: your one-time PIN is ${data.sim_otp}`, 'info');
    let left = data.expires_in;
    const render = () => ($('otp-timer').textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`);
    render();
    clearInterval(otpTimer);
    otpTimer = setInterval(() => {
      left -= 1;
      render();
      if (left <= 0) { clearInterval(otpTimer); showError('otp', 'This one-time PIN expired. Close and start again.'); }
    }, 1000);
  }
  steps.otp.querySelector('[data-cancel-otp]').addEventListener('click', close);
  async function submitOtp() {
    busy = true;
    const { ok, data } = await BB.post(root.dataset.confirmUrl, { otp: entry.otp });
    busy = false;
    resetEntry('otp');
    if (ok && data.status === 'ok') { clearInterval(otpTimer); return showReceipt(data); }
    showError('otp', data.error || 'Could not verify the code.');
    if (data.cancelled) { clearInterval(otpTimer); setTimeout(close, 2200); }
  }

  // ---------------- Receipt & post-transaction security ----------------
  function showReceipt(data) {
    const r = data.receipt;
    $('r-title').textContent = copy[r.kind].done;
    $('r-amount').textContent = r.amount_display;
    $('r-ref').textContent = r.reference;
    $('r-time').textContent = r.time_display;
    $('r-balance').textContent = data.balance_display;
    wipeSecrets();
    showStep('receipt');

    $('balance-amount').textContent = data.balance_display;
    $('daily-left').textContent = data.daily_left_display;
    $('withdrawn-today').textContent = peso.format(parseFloat(data.withdrawn_today));
    const bar = $('limit-bar');
    bar.style.width = `${Math.min(100, (parseFloat(data.withdrawn_today) / parseFloat(bar.dataset.limit)) * 100)}%`;
    prependRow(r);

    BB.maskAll();
    startCooldown(data.cooldown);
    BB.toast(`${copy[r.kind].done} Ref ${r.reference}`, 'success');
  }

  function prependRow(r) {
    const isDep = r.kind === 'deposit';
    const [datePart, timePart] = r.time_display.split(' · ');
    const tr = document.createElement('tr');
    tr.dataset.kind = r.kind;
    tr.className = 'is-new';
    tr.innerHTML = `
      <td class="hidden sm:table-cell whitespace-nowrap text-xs text-flag-950/70"><span data-f="date"></span><span class="block text-[10px]" data-f="time"></span></td>
      <td><span class="flex items-center gap-2.5">
        <span class="grid place-items-center w-10 h-10 rounded-full border-2 border-flag-900 shrink-0 ${isDep ? 'bg-flag-100 text-flag-700' : 'bg-hero-100 text-hero-600'}"><i data-lucide="${isDep ? 'arrow-down' : 'arrow-up'}" class="w-5 h-5"></i></span>
        <span class="min-w-0"><b class="block" data-f="label"></b><span class="block sm:hidden text-[11px] text-flag-950/60" data-f="mdate"></span><span class="font-mono text-[10px] text-flag-950/55" data-f="ref"></span></span>
      </span></td>
      <td class="text-right font-extrabold tabular-nums whitespace-nowrap ${isDep ? 'text-flag-700' : 'text-hero-600'}" data-f="amount"></td>
      <td class="hidden sm:table-cell text-right tabular-nums text-flag-950/70 whitespace-nowrap" data-f="bal"></td>
      <td class="text-center"><span class="pill-ok">Success</span></td>`;
    const set = (f, v) => (tr.querySelector(`[data-f="${f}"]`).textContent = v);
    set('date', (datePart || '').replace(/, \d{4}$/, ''));
    set('time', timePart || '');
    set('mdate', `${(datePart || '').replace(/, \d{4}$/, '')}, ${timePart || ''}`);
    set('label', isDep ? 'Deposit' : 'Withdrawal');
    set('ref', r.reference);
    set('amount', `${isDep ? '+' : '−'}${r.amount_display}`);
    set('bal', r.balance_after_display);
    $('txn-list').prepend(tr);
    $('txn-empty').classList.add('hidden');
    applyFilter();
    BB.icons();
  }

  function startCooldown(seconds) {
    const buttons = document.querySelectorAll('[data-open-txn]');
    const note = $('cooldown-note');
    let left = seconds;
    buttons.forEach((b) => (b.disabled = true));
    note.classList.remove('hidden');
    const tick = () => {
      note.textContent = `Security pause: next transaction in ${left}s`;
      if (left-- <= 0) {
        clearInterval(cooldownTimer);
        buttons.forEach((b) => (b.disabled = false));
        note.classList.add('hidden');
      }
    };
    clearInterval(cooldownTimer);
    tick();
    cooldownTimer = setInterval(tick, 1000);
  }

  // ---------------- History filters ----------------
  let filter = 'all';
  function applyFilter() {
    const rows = [...document.querySelectorAll('#txn-list tr')];
    let shown = 0;
    rows.forEach((tr) => {
      const match = filter === 'all' || tr.dataset.kind === filter;
      tr.classList.toggle('hidden', !match);
      if (match) shown++;
    });
    $('txn-filter-empty').classList.toggle('hidden', !(rows.length && shown === 0));
  }
  document.querySelectorAll('[data-filter]').forEach((btn) =>
    btn.addEventListener('click', () => {
      filter = btn.dataset.filter;
      document.querySelectorAll('[data-filter]').forEach((b) => b.classList.toggle('is-active', b === btn));
      applyFilter();
    })
  );
})();
