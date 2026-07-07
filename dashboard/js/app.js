/**
 * app.js — GST ACT AI Dashboard Logic
 * All data from real Flask/Supabase API. No mock data.
 */

/* ══════════════════════════════════════════════
   STATE
   ══════════════════════════════════════════════ */
let currentPage       = 'bills';
let currentBillDetail = null;
let allBills          = [];          // raw from API
let filteredBills     = [];
let otpTimerInterval  = null;
let purchaseChart     = null;
let gstChart          = null;
let autoRefreshTimer  = null;
let loginPhone        = '';          // Stores phone number across Send/Verify OTP steps
let selectedBillsMonth = '';         // '' = all months, 'YYYY-MM' = specific month

/* ══════════════════════════════════════════════
   INIT
   ══════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  // OTP box keyboard nav
  for (let i = 0; i < 6; i++) {
    const box = document.getElementById(`otp-${i}`);
    box.addEventListener('input', e => {
      const v = e.target.value.replace(/\D/g, '');
      e.target.value = v.slice(-1);
      if (v && i < 5) document.getElementById(`otp-${i + 1}`).focus();
    });
    box.addEventListener('keydown', e => {
      if (e.key === 'Backspace' && !box.value && i > 0)
        document.getElementById(`otp-${i - 1}`).focus();
      if (e.key === 'Enter') handleVerifyOtp();
    });
    box.addEventListener('paste', e => {
      e.preventDefault();
      const pasted = (e.clipboardData || window.clipboardData)
        .getData('text').replace(/\D/g, '').slice(0, 6);
      for (let j = 0; j < pasted.length; j++) {
        const t = document.getElementById(`otp-${j}`);
        if (t) t.value = pasted[j];
      }
      if (pasted.length === 6) handleVerifyOtp();
    });
  }
  const phoneInput = document.getElementById('phone-input');
  if (phoneInput) {
    phoneInput.addEventListener('keydown', e => {
      if (e.key === 'Enter') handleSendOtp();
    });
    phoneInput.addEventListener('paste', e => {
      e.preventDefault();
      let pasted = (e.clipboardData || window.clipboardData)
        .getData('text').trim().replace(/[\s-()]/g, '');
      if (pasted.startsWith('+91')) {
        pasted = pasted.slice(3);
      } else if (pasted.startsWith('91') && pasted.length > 10) {
        pasted = pasted.slice(2);
      } else if (pasted.startsWith('0') && pasted.length > 10) {
        pasted = pasted.slice(1);
      } else if (pasted.length > 10) {
        pasted = pasted.slice(-10);
      }
      phoneInput.value = pasted.slice(0, 10);
    });
  }

  // If already logged in (token in localStorage), go straight to app
  if (isLoggedIn()) {
    showApp();
  }

  // Sync session across multiple browser tabs
  window.addEventListener('storage', e => {
    if (e.key === 'gst_phone' || e.key === 'gst_token') {
      location.reload();
    }
  });
});

/* ══════════════════════════════════════════════
   AUTH
   ══════════════════════════════════════════════ */
async function handleSendOtp(isResend = false) {
  const phoneInput = document.getElementById('phone-input');
  let rawPhone = (phoneInput?.value || '').trim().replace(/[\s-()]/g, '');
  if (rawPhone.startsWith('+91')) {
    rawPhone = rawPhone.slice(3);
  } else if (rawPhone.startsWith('91') && rawPhone.length > 10) {
    rawPhone = rawPhone.slice(2);
  } else if (rawPhone.startsWith('0') && rawPhone.length > 10) {
    rawPhone = rawPhone.slice(1);
  }
  const phone = rawPhone;
  if (phoneInput) phoneInput.value = phone;
  loginPhone = phone; // Persist globally to prevent browser autofill overwrites

  if (!isResend && (phone.length !== 10 || !/^\d{10}$/.test(phone))) {
    showToast('Enter a valid 10-digit number', 'error');
    phoneInput.focus();
    return;
  }

  const btn = document.getElementById('send-otp-btn');
  setButtonLoading(btn, true, 'Sending...');

  try {
    const data = await apiSendOtp(phone || SESSION.phone.replace('+91', ''));

    if (!data.success) {
      showToast(data.error || 'Could not send OTP', 'error');
      setButtonLoading(btn, false);
      return;
    }

    // Show OTP page
    const displayPhone = `+91 ${phone.slice(0,5)} ${phone.slice(5)}`;
    document.getElementById('otp-phone-display').textContent = displayPhone;
    document.getElementById('login-page').classList.remove('active');
    document.getElementById('otp-page').classList.add('active');
    startOtpTimer();

    // Dev mode: autofill OTP boxes if server returned the OTP
    if (data.dev_otp) {
      for (let i = 0; i < 6; i++) {
        const b = document.getElementById(`otp-${i}`);
        if (b) b.value = data.dev_otp[i] || '';
      }
      showToast(`Dev mode: OTP autofilled (${data.dev_otp})`, 'info');
    } else {
      document.getElementById('otp-0').focus();
      showToast(`OTP sent to ${displayPhone}`, 'success');
    }


  } catch (err) {
    showToast('Network error — is the Flask server running?', 'error');
  } finally {
    setButtonLoading(btn, false);
  }
}

async function handleVerifyOtp() {
  // Use the globally saved loginPhone value to bypass any browser/autofill DOM manipulation
  const phone = loginPhone || document.getElementById('phone-input').value.trim();
  let otp = '';
  for (let i = 0; i < 6; i++) otp += document.getElementById(`otp-${i}`).value;

  if (otp.length !== 6) {
    showToast('Enter the complete 6-digit OTP', 'error');
    return;
  }

  const btn = document.getElementById('verify-btn');
  setButtonLoading(btn, true, 'Verifying...');

  try {
    const data = await apiVerifyOtp(phone, otp);

    if (!data.success) {
      showToast(data.error || 'Verification failed', 'error');
      setButtonLoading(btn, false);
      return;
    }

    saveSession(data.whatsapp_number, data.token);
    clearInterval(otpTimerInterval);

    // Fade out auth, fade in app
    const auth = document.getElementById('auth-screen');
    auth.style.transition = 'opacity 0.35s ease, transform 0.35s ease';
    auth.style.opacity = '0';
    auth.style.transform = 'scale(0.96)';

    setTimeout(() => {
      auth.style.display = 'none';
      showApp();
    }, 350);

    showToast('Welcome! 🎉', 'success');

  } catch (err) {
    showToast('Network error — is the Flask server running?', 'error');
  } finally {
    setButtonLoading(btn, false);
  }
}

function showApp() {
  const app = document.getElementById('app');
  app.classList.remove('hidden');
  app.style.opacity = '0';
  requestAnimationFrame(() => {
    app.style.transition = 'opacity 0.3s ease';
    app.style.opacity = '1';
  });

  // Update sidebar profile
  const phone = SESSION.phone;
  document.getElementById('sidebar-phone').textContent = phone;
  document.getElementById('sidebar-name').textContent = SESSION.name || 'Merchant';
  const initials = phone.slice(-4, -2) || 'ME';
  document.getElementById('sidebar-avatar').textContent = initials;

  populateMonthPicker();
  setMonthLabels();
  loadBillsPage();
}

function showLoginPage() {
  document.getElementById('otp-page').classList.remove('active');
  document.getElementById('login-page').classList.add('active');
  clearInterval(otpTimerInterval);
  clearOtpBoxes();
}

function startOtpTimer() {
  let sec = 30;
  const timerEl  = document.getElementById('timer-count');
  const timerWrap = document.getElementById('otp-timer');
  const resendBtn = document.getElementById('resend-btn');
  timerWrap.style.display = 'block';
  resendBtn.style.display = 'none';
  clearInterval(otpTimerInterval);
  otpTimerInterval = setInterval(() => {
    timerEl.textContent = --sec;
    if (sec <= 0) {
      clearInterval(otpTimerInterval);
      timerWrap.style.display = 'none';
      resendBtn.style.display = 'block';
    }
  }, 1000);
}

function clearOtpBoxes() {
  for (let i = 0; i < 6; i++) {
    const b = document.getElementById(`otp-${i}`);
    if (b) b.value = '';
  }
}

function logout() {
  clearSession();
  clearInterval(autoRefreshTimer);
  showToast('Logged out', 'info');
  setTimeout(() => {
    document.getElementById('app').classList.add('hidden');
    const auth = document.getElementById('auth-screen');
    auth.style.cssText = '';
    document.getElementById('otp-page').classList.remove('active');
    document.getElementById('login-page').classList.add('active');
    document.getElementById('phone-input').value = '';
    clearOtpBoxes();
    if (purchaseChart) { purchaseChart.destroy(); purchaseChart = null; }
    if (gstChart)      { gstChart.destroy();      gstChart      = null; }
  }, 600);
}

/* ══════════════════════════════════════════════
   NAVIGATION
   ══════════════════════════════════════════════ */
function showPage(pageName, navEl) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));

  const pg = document.getElementById(`page-${pageName}`);
  if (pg) pg.classList.add('active');
  if (navEl) navEl.classList.add('active');

  currentPage = pageName;
  document.querySelector('.main-content').scrollTop = 0;

  if (pageName === 'summary') loadSummaryPage();
}

function updateBottomNav(activeId) {
  document.querySelectorAll('.bottom-nav-item').forEach(i => i.classList.remove('active'));
  const t = document.getElementById(activeId);
  if (t) t.classList.add('active');
}

function setMonthLabels() {
  // Update bills month label based on selected month
  const billsLabel = document.getElementById('bills-month-label');
  if (billsLabel) {
    if (!selectedBillsMonth || selectedBillsMonth === 'all') {
      billsLabel.textContent = 'All Months';
    } else {
      const [yr, mo] = selectedBillsMonth.split('-');
      const d = new Date(parseInt(yr), parseInt(mo) - 1, 1);
      billsLabel.textContent = d.toLocaleDateString('en-IN', { month: 'long', year: 'numeric' });
    }
  }
  // Summary label always shows current month
  const summaryLabel = document.getElementById('summary-month-label');
  if (summaryLabel) summaryLabel.textContent = getCurrentMonthLabel();
}

function populateMonthPicker() {
  const picker = document.getElementById('bills-month-picker');
  if (!picker) return;

  const now = new Date();
  let options = '<option value="all">All Months</option>';

  // Generate last 12 months (current month first)
  for (let i = 0; i < 12; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    const val = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
    const label = d.toLocaleDateString('en-IN', { month: 'long', year: 'numeric' });
    const selected = (i === 0) ? ' selected' : '';
    options += `<option value="${val}"${selected}>${label}</option>`;
  }

  picker.innerHTML = options;
  selectedBillsMonth = getCurrentMonthParam(); // Default to current month
}

function onBillsMonthChange() {
  const picker = document.getElementById('bills-month-picker');
  selectedBillsMonth = picker.value;
  setMonthLabels();
  loadBillsPage(true);
}

/* ══════════════════════════════════════════════
   BILLS PAGE
   ══════════════════════════════════════════════ */
async function loadBillsPage(forceRefresh = false) {
  // Show loading state on stat cards
  document.querySelectorAll('#bills-stats-grid .stat-card').forEach(c => c.classList.add('skeleton-loading'));
  showBillsState('loading');

  try {
    const month = (selectedBillsMonth === 'all') ? '' : (selectedBillsMonth || getCurrentMonthParam());
    const data  = await apiFetchBills(month);

    if (!data.success) throw new Error(data.error || 'Failed to load bills');

    allBills = data.bills || [];
    filteredBills = [...allBills];

    // Update summary cards
    const s = data.summary;
    document.getElementById('val-total-bills').textContent     = s.total_bills;
    document.getElementById('val-total-purchases').textContent = fmtINR(s.total_purchases);
    document.getElementById('val-total-igst').textContent      = fmtINR(s.total_igst);
    document.getElementById('val-bills-trend').textContent     = `${s.total_bills} bill${s.total_bills !== 1 ? 's' : ''} this month`;

    // Also update empty state WhatsApp number
    if (data.bills.length === 0) {
      fetchAndSetWaNumber();
    }

    document.querySelectorAll('#bills-stats-grid .stat-card').forEach(c => c.classList.remove('skeleton-loading'));

    filterBills(); // Render with current search/filter

    if (forceRefresh) showToast('Bills refreshed ✓', 'success');

    // Auto-refresh every 60s when on bills page
    clearInterval(autoRefreshTimer);
    autoRefreshTimer = setInterval(() => {
      if (currentPage === 'bills') loadBillsPage();
    }, 60000);

  } catch (err) {
    document.querySelectorAll('#bills-stats-grid .stat-card').forEach(c => c.classList.remove('skeleton-loading'));
    showBillsState('error');
    document.getElementById('bills-error-msg').textContent =
      err.message + ' — Make sure Flask is running on localhost:5000';
    console.error('[Bills] Error:', err);
  }
}

function showBillsState(state) {
  // state: 'loading' | 'data' | 'empty' | 'error'
  const table   = document.getElementById('bills-table-container');
  const mobile  = document.getElementById('bills-mobile-cards');
  const empty   = document.getElementById('bills-empty');
  const error   = document.getElementById('bills-error');

  table.classList.remove('hidden');
  mobile.style.display = 'flex';
  empty.classList.add('hidden');
  error.classList.add('hidden');

  if (state === 'empty') {
    table.classList.add('hidden');
    mobile.style.display = 'none';
    empty.classList.remove('hidden');
  } else if (state === 'error') {
    table.classList.add('hidden');
    mobile.style.display = 'none';
    error.classList.remove('hidden');
  } else if (state === 'loading') {
    // Show skeleton in table
    document.getElementById('bills-tbody').innerHTML = `
      <tr class="skeleton-row"><td colspan="6"><div class="skeleton-line"></div></td></tr>
      <tr class="skeleton-row"><td colspan="6"><div class="skeleton-line"></div></td></tr>
      <tr class="skeleton-row"><td colspan="6"><div class="skeleton-line"></div></td></tr>
      <tr class="skeleton-row"><td colspan="6"><div class="skeleton-line"></div></td></tr>
    `;
    mobile.innerHTML = '';
  }
}

function renderBillsTable(bills) {
  const tbody = document.getElementById('bills-tbody');
  tbody.innerHTML = bills.map(b => `
    <tr onclick="openBillDetail('${escHtml(b.id)}')">
      <td>
        <div style="display:flex;align-items:center;gap:10px;">
          <div style="width:32px;height:32px;border-radius:8px;background:var(--brand-blue-light);color:var(--brand-blue);display:flex;align-items:center;justify-content:center;font-size:0.7rem;font-weight:800;flex-shrink:0;text-transform:uppercase;">
            ${escHtml((b.vendor_name || 'UN').slice(0, 2))}
          </div>
          <div>
            <div style="font-weight:600;color:var(--gray-800);">${escHtml(b.vendor_name || '—')}</div>
            <div style="font-size:0.75rem;color:var(--gray-400);">${escHtml(b.invoice_number || '—')}</div>
          </div>
        </div>
      </td>
      <td>${fmtDate(b.invoice_date)}</td>
      <td style="font-weight:700;color:var(--gray-900);">${fmtINR(b.total_amount)}</td>
      <td style="color:var(--amber);font-weight:600;">${fmtINR(b.igst)}</td>
      <td><span class="status-chip ${b.status}">${capitalize(b.status)}</span></td>
      <td><button class="btn-view-detail" onclick="event.stopPropagation();openBillDetail('${escHtml(b.id)}')">View →</button></td>
    </tr>
  `).join('');
}

function renderBillsMobileCards(bills) {
  const container = document.getElementById('bills-mobile-cards');
  container.innerHTML = bills.map(b => `
    <div class="bill-mobile-card" onclick="openBillDetail('${escHtml(b.id)}')">
      <div class="bill-mobile-avatar">${escHtml((b.vendor_name || 'UN').slice(0, 2))}</div>
      <div class="bill-mobile-info">
        <div class="bill-mobile-vendor">${escHtml(b.vendor_name || '—')}</div>
        <div class="bill-mobile-date">${fmtDate(b.invoice_date)} · ${escHtml(b.invoice_number || '—')}</div>
      </div>
      <div class="bill-mobile-right">
        <div class="bill-mobile-amount">${fmtINR(b.total_amount)}</div>
        <span class="status-chip ${b.status}">${capitalize(b.status)}</span>
      </div>
    </div>
  `).join('');
}

function filterBills() {
  const search = document.getElementById('bills-search').value.toLowerCase().trim();
  const filter = document.getElementById('bills-filter').value;
  const sort   = document.getElementById('bills-sort').value;

  let bills = [...allBills];

  if (search) bills = bills.filter(b =>
    (b.vendor_name || '').toLowerCase().includes(search) ||
    (b.invoice_number || '').toLowerCase().includes(search)
  );

  if (filter !== 'all') bills = bills.filter(b => b.status === filter);

  bills.sort((a, b) => {
    switch (sort) {
      case 'date-desc':   return new Date(b.invoice_date || 0) - new Date(a.invoice_date || 0);
      case 'date-asc':    return new Date(a.invoice_date || 0) - new Date(b.invoice_date || 0);
      case 'amount-desc': return (b.total_amount || 0) - (a.total_amount || 0);
      case 'amount-asc':  return (a.total_amount || 0) - (b.total_amount || 0);
      default: return 0;
    }
  });

  filteredBills = bills;

  if (bills.length === 0 && allBills.length === 0) {
    showBillsState('empty');
  } else if (bills.length === 0) {
    // filtered empty but data exists
    showBillsState('data');
    document.getElementById('bills-tbody').innerHTML = `
      <tr><td colspan="6" style="text-align:center;padding:32px;color:var(--gray-400);">
        No bills match your search/filter.
      </td></tr>
    `;
    document.getElementById('bills-mobile-cards').innerHTML = `
      <div style="text-align:center;padding:32px;color:var(--gray-400);">No bills match your search.</div>
    `;
  } else {
    showBillsState('data');
    renderBillsTable(bills);
    renderBillsMobileCards(bills);
  }
}

/* ══════════════════════════════════════════════
   BILL DETAIL PAGE
   ══════════════════════════════════════════════ */
async function openBillDetail(billId) {
  // Navigate first
  showPage('detail', null);
  document.getElementById('nav-bills').classList.add('active');

  // Show skeleton while loading
  document.getElementById('detail-vendor-title').textContent = 'Loading...';
  document.getElementById('detail-vendor').textContent  = '—';
  document.getElementById('detail-invoice-no').textContent = '—';
  document.getElementById('detail-date').textContent = '—';
  document.getElementById('detail-gstin').textContent = '—';
  document.getElementById('detail-total-amount').textContent = '—';
  document.getElementById('detail-total-igst').textContent = '—';
  document.getElementById('detail-items-count').textContent = 'Loading...';
  document.getElementById('detail-tbody').innerHTML = `
    <tr class="skeleton-row"><td colspan="7"><div class="skeleton-line"></div></td></tr>
    <tr class="skeleton-row"><td colspan="7"><div class="skeleton-line"></div></td></tr>
    <tr class="skeleton-row"><td colspan="7"><div class="skeleton-line"></div></td></tr>
  `;

  try {
    const data = await apiFetchBill(billId);
    if (!data.success) throw new Error(data.error);

    const bill = data.bill;
    currentBillDetail = bill;

    document.getElementById('detail-vendor-title').textContent  = bill.vendor_name || 'Bill Detail';
    document.getElementById('detail-vendor').textContent         = bill.vendor_name || '—';
    document.getElementById('detail-invoice-no').textContent     = bill.invoice_number || '—';
    document.getElementById('detail-date').textContent           = fmtDate(bill.invoice_date);
    document.getElementById('detail-gstin').textContent          = bill.vendor_gstin || '—';
    document.getElementById('detail-total-amount').textContent   = fmtINR(bill.total_amount);
    document.getElementById('detail-total-igst').textContent     = fmtINR(bill.igst);

    // Line items — support both field name conventions from Gemini/Supabase
    const items = bill.line_items || [];
    document.getElementById('detail-items-count').textContent = `${items.length} item${items.length !== 1 ? 's' : ''}`;

    document.getElementById('detail-tbody').innerHTML = items.map((item, idx) => {
      const qty      = item.quantity ?? item.qty ?? '—';
      const rate     = item.rate ?? item.rate_per_unit ?? null;
      const taxable  = item.amount ?? item.taxable_value ?? null;
      const igstAmt  = item.gst_amount ?? item.igst_amount ?? null;
      const hsn      = item.hsn_sac ?? item.hsn_code ?? '—';
      const desc     = item.description ?? item.item_name ?? '—';

      return `
        <tr>
          <td class="sticky-col" style="font-weight:700;color:var(--gray-400);background:white;">${idx + 1}</td>
          <td style="font-weight:600;color:var(--gray-800);min-width:180px;white-space:normal;">${escHtml(desc)}</td>
          <td class="mono" style="color:var(--gray-500);font-size:0.82rem;">${escHtml(String(hsn))}</td>
          <td style="text-align:right;">${qty}</td>
          <td style="text-align:right;font-weight:600;">${rate != null ? fmtINR(rate) : '—'}</td>
          <td style="text-align:right;font-weight:700;color:var(--gray-800);">${taxable != null ? fmtINR(taxable) : '—'}</td>
          <td style="text-align:right;color:var(--amber);font-weight:700;">${igstAmt != null ? fmtINR(igstAmt) : '—'}</td>
        </tr>
      `;
    }).join('');

    // Summary
    const taxableTotal = items.reduce((s, i) => s + parseFloat(i.amount ?? i.taxable_value ?? 0), 0);
    const igstTotal    = items.reduce((s, i) => s + parseFloat(i.gst_amount ?? i.igst_amount ?? 0), 0);

    document.getElementById('detail-taxable').textContent    = fmtINR(taxableTotal);
    document.getElementById('detail-igst-total').textContent = fmtINR(bill.igst || igstTotal);
    document.getElementById('detail-grand-total').textContent = fmtINR(bill.total_amount);

  } catch (err) {
    showToast('Could not load bill detail: ' + err.message, 'error');
    showPage('bills', document.getElementById('nav-bills'));
  }
}

/* ══════════════════════════════════════════════
   GST SUMMARY PAGE
   ══════════════════════════════════════════════ */
let summaryLoaded = false;

async function loadSummaryPage() {
  if (summaryLoaded) return; // Don't reload on every nav click

  // Show skeletons
  document.querySelectorAll('#summary-stats-grid .stat-card').forEach(c => c.classList.add('skeleton-loading'));

  try {
    const data = await apiFetchSummary();
    if (!data.success) throw new Error(data.error);

    const s = data.summary;
    document.getElementById('sum-total-purchases').textContent = fmtINR(s.total_amount);
    document.getElementById('sum-total-igst').textContent      = fmtINR(s.igst);
    document.getElementById('sum-gstr4').textContent           = fmtINR(s.gstr4);

    document.querySelectorAll('#summary-stats-grid .stat-card').forEach(c => c.classList.remove('skeleton-loading'));

    // Charts
    const t = data.trend;
    renderPurchaseChart(t.labels, t.purchases);
    renderGstChart(t.labels, t.igst);

    summaryLoaded = true;

  } catch (err) {
    document.querySelectorAll('#summary-stats-grid .stat-card').forEach(c => c.classList.remove('skeleton-loading'));
    showToast('Could not load summary: ' + err.message, 'error');
    console.error('[Summary] Error:', err);
  }

  // Load WhatsApp info for QR
  loadWhatsappInfo();
}

async function loadWhatsappInfo() {
  try {
    const data = await apiFetchWhatsappInfo();
    if (!data.success) return;

    const num  = data.whatsapp_number;
    const link = data.wa_link;

    document.getElementById('wa-number-display').textContent = num;
    const anchor = document.getElementById('wa-open-link');
    if (anchor) anchor.href = link;

    // Render QR code
    const canvas = document.getElementById('wa-qr-canvas');
    await QRCode.toCanvas(canvas, link, {
      width: 200,
      margin: 2,
      color: { dark: '#111827', light: '#ffffff' }
    });

    document.getElementById('qr-wrapper').querySelector('.qr-loading').classList.add('hidden');
    canvas.classList.remove('hidden');

    // Update empty state WhatsApp number
    const emptyWa = document.getElementById('empty-wa-number');
    if (emptyWa) emptyWa.textContent = num;

  } catch (err) {
    console.warn('[WhatsApp Info]', err.message);
    const waNum = document.getElementById('wa-number-display');
    if (waNum) waNum.textContent = 'Start Flask server to see number';
  }
}

async function fetchAndSetWaNumber() {
  try {
    const data = await apiFetchWhatsappInfo();
    if (data.success) {
      const el = document.getElementById('empty-wa-number');
      if (el) el.textContent = data.whatsapp_number;
    }
  } catch (_) {}
}

/* ══════════════════════════════════════════════
   CHARTS
   ══════════════════════════════════════════════ */
function renderPurchaseChart(labels, values) {
  const ctx = document.getElementById('purchase-chart').getContext('2d');
  if (purchaseChart) purchaseChart.destroy();

  purchaseChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: 'Purchases (₹)',
        data: values,
        backgroundColor: ctx => {
          const { ctx: c, chartArea } = ctx.chart;
          if (!chartArea) return 'rgba(26,86,219,0.7)';
          const g = c.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
          g.addColorStop(0, 'rgba(26,86,219,0.85)');
          g.addColorStop(1, 'rgba(26,86,219,0.15)');
          return g;
        },
        borderRadius: 8,
        borderSkipped: false,
      }]
    },
    options: chartOptions(v =>
      v >= 100000 ? '₹' + (v / 100000).toFixed(1) + 'L'
      : v >= 1000 ? '₹' + (v / 1000).toFixed(0) + 'K'
      : '₹' + v
    )
  });
}

function renderGstChart(labels, values) {
  const ctx = document.getElementById('gst-chart').getContext('2d');
  if (gstChart) gstChart.destroy();

  gstChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'IGST (₹)',
        data: values,
        borderColor: '#f59e0b',
        backgroundColor: ctx => {
          const { ctx: c, chartArea } = ctx.chart;
          if (!chartArea) return 'rgba(245,158,11,0.1)';
          const g = c.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
          g.addColorStop(0, 'rgba(245,158,11,0.25)');
          g.addColorStop(1, 'rgba(245,158,11,0.0)');
          return g;
        },
        borderWidth: 2.5,
        pointBackgroundColor: '#f59e0b',
        pointBorderColor: '#fff',
        pointBorderWidth: 2,
        pointRadius: 5,
        pointHoverRadius: 7,
        fill: true,
        tension: 0.4,
      }]
    },
    options: chartOptions(v => v >= 1000 ? '₹' + (v / 1000).toFixed(0) + 'K' : '₹' + v)
  });
}

function chartOptions(tickFormatter) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: '#1f2937',
        titleColor: '#9ca3af',
        bodyColor: '#fff',
        padding: 12,
        cornerRadius: 8,
        callbacks: { label: ctx => ' ' + fmtINR(ctx.parsed.y) }
      }
    },
    scales: {
      x: {
        grid: { display: false },
        border: { display: false },
        ticks: { color: '#9ca3af', font: { size: 12, family: 'Inter', weight: '500' } }
      },
      y: {
        grid: { color: '#f3f4f6' },
        border: { display: false },
        ticks: { color: '#9ca3af', font: { size: 11, family: 'Inter' }, callback: tickFormatter }
      }
    },
    animation: { duration: 800, easing: 'easeOutQuart' }
  };
}

/* ══════════════════════════════════════════════
   ACTIONS
   ══════════════════════════════════════════════ */
function escapeCSV(val) {
  if (val === null || val === undefined) return '';
  let str = String(val);
  if (str.includes('"') || str.includes(',') || str.includes('\n') || str.includes('\r')) {
    str = '"' + str.replace(/"/g, '""') + '"';
  }
  return str;
}

function downloadSummaryExcel() {
  const billsToExport = filteredBills.length > 0 ? filteredBills : allBills;
  if (!billsToExport || billsToExport.length === 0) {
    showToast('No bills data to download', 'error');
    return;
  }

  // CA-ready format: one row per line item with HSN, GST rate, and split tax amounts
  const headers = [
    'Invoice Number', 'Invoice Date', 'Vendor Name', 'GSTIN',
    'Item Description', 'HSN / SAC Code', 'Quantity', 'Rate (INR)',
    'Taxable Value (INR)', 'GST Rate (%)', 'CGST (INR)', 'SGST (INR)', 'IGST (INR)'
  ];

  const rows = [];
  for (const b of billsToExport) {
    const items = b.line_items || [];
    if (items.length === 0) {
      // Bill with no line items — emit one row with bill-level totals
      rows.push([
        b.invoice_number || '', b.invoice_date || '',
        b.vendor_name || '', b.vendor_gstin || '',
        '(No line items)', '', '', '',
        b.total_amount || 0, '',
        b.cgst || 0, b.sgst || 0, b.igst || 0
      ]);
    } else {
      // Compute per-item CGST / SGST / IGST split from bill-level totals
      // If IGST is present, CGST & SGST are typically 0, and vice versa.
      const billCGST = parseFloat(b.cgst || 0);
      const billSGST = parseFloat(b.sgst || 0);
      const billIGST = parseFloat(b.igst || 0);
      const totalItemTax = items.reduce((s, i) =>
        s + parseFloat(i.gst_amount ?? i.igst_amount ?? 0), 0);

      for (const item of items) {
        const desc     = item.description ?? item.item_name ?? '';
        const hsn      = item.hsn_sac ?? item.hsn_code ?? '';
        const qty      = item.quantity ?? item.qty ?? '';
        const rate     = item.rate ?? item.rate_per_unit ?? '';
        const taxable  = item.amount ?? item.taxable_value ?? 0;
        const gstRate  = item.gst_rate ?? '';
        const itemTax  = parseFloat(item.gst_amount ?? item.igst_amount ?? 0);

        // Proportional tax split: distribute bill-level CGST/SGST/IGST
        // across items based on each item's share of total item tax
        let itemCGST = 0, itemSGST = 0, itemIGST = 0;
        if (totalItemTax > 0) {
          const ratio = itemTax / totalItemTax;
          itemCGST = +(billCGST * ratio).toFixed(2);
          itemSGST = +(billSGST * ratio).toFixed(2);
          itemIGST = +(billIGST * ratio).toFixed(2);
        } else if (items.length === 1) {
          // Single item — assign all bill-level tax
          itemCGST = billCGST;
          itemSGST = billSGST;
          itemIGST = billIGST;
        }

        rows.push([
          b.invoice_number || '', b.invoice_date || '',
          b.vendor_name || '', b.vendor_gstin || '',
          desc, hsn, qty, rate,
          taxable, gstRate,
          itemCGST, itemSGST, itemIGST
        ]);
      }
    }
  }

  const csvContent = [
    headers.map(escapeCSV).join(','),
    ...rows.map(row => row.map(escapeCSV).join(','))
  ].join('\n');

  const blob = new Blob([new Uint8Array([0xEF, 0xBB, 0xBF]), csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  const dateStr = new Date().toISOString().slice(0, 7);
  link.setAttribute('href', url);
  link.setAttribute('download', `gst_workbook_${dateStr}.csv`);
  link.style.visibility = 'hidden';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  
  showToast('CA-ready GST workbook downloaded ✓', 'success');
}

function downloadBillItemsExcel() {
  if (!currentBillDetail) {
    showToast('No bill details loaded', 'error');
    return;
  }

  const bill = currentBillDetail;
  const items = bill.line_items || [];

  const metadata = [
    ['Vendor Name', bill.vendor_name || ''],
    ['Vendor GSTIN', bill.vendor_gstin || ''],
    ['Invoice Number', bill.invoice_number || ''],
    ['Invoice Date', bill.invoice_date || ''],
    ['Total Amount', bill.total_amount || 0],
    ['Total IGST', bill.igst || 0],
    [],
    ['#', 'Item Name / Description', 'HSN / SAC', 'Quantity', 'Rate (INR)', 'Taxable Value (INR)', 'IGST Rate (%)', 'IGST Amount (INR)']
  ];

  const rows = items.map((item, idx) => {
    const qty      = item.quantity ?? item.qty ?? '';
    const rate     = item.rate ?? item.rate_per_unit ?? 0;
    const taxable  = item.amount ?? item.taxable_value ?? 0;
    const igstRate = item.gst_rate ?? '';
    const igstAmt  = item.gst_amount ?? item.igst_amount ?? 0;
    const hsn      = item.hsn_sac ?? item.hsn_code ?? '';
    const desc     = item.description ?? item.item_name ?? '';
    return [
      idx + 1,
      desc,
      hsn,
      qty,
      rate,
      taxable,
      igstRate,
      igstAmt
    ];
  });

  const csvContent = [
    ...metadata.map(row => row.map(escapeCSV).join(',')),
    ...rows.map(row => row.map(escapeCSV).join(','))
  ].join('\n');

  const blob = new Blob([new Uint8Array([0xEF, 0xBB, 0xBF]), csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  const invName = (bill.invoice_number || 'bill').replace(/[^a-zA-Z0-9]/g, '_');
  link.setAttribute('href', url);
  link.setAttribute('download', `bill_${invName}_details.csv`);
  link.style.visibility = 'hidden';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);

  showToast('Bill Excel downloaded ✓', 'success');
}

function downloadExcel() {
  showToast('Generating Excel...', 'info');
  setTimeout(() => {
    try {
      if (currentPage === 'detail') {
        downloadBillItemsExcel();
      } else {
        downloadSummaryExcel();
      }
    } catch (e) {
      console.error(e);
      showToast('Error generating Excel', 'error');
    }
  }, 500);
}

function printBill() {
  showToast('Opening print dialog...', 'info');
  setTimeout(() => window.print(), 400);
}

/* ══════════════════════════════════════════════
   TOAST
   ══════════════════════════════════════════════ */
let toastTimeout = null;

function showToast(message, type = 'default') {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.className = 'toast';
  if (type === 'success') toast.classList.add('toast-success');
  else if (type === 'error') toast.classList.add('toast-error');
  else if (type === 'info')  toast.classList.add('toast-info');

  const nav = document.getElementById('bottom-nav');
  toast.style.bottom = (nav && window.innerWidth <= 768)
    ? (nav.offsetHeight + 12) + 'px'
    : '24px';

  requestAnimationFrame(() => toast.classList.add('visible'));
  clearTimeout(toastTimeout);
  toastTimeout = setTimeout(() => toast.classList.remove('visible'), 3200);
}

/* ══════════════════════════════════════════════
   UTILITIES
   ══════════════════════════════════════════════ */
function capitalize(str) {
  if (!str) return '';
  return str.charAt(0).toUpperCase() + str.slice(1).toLowerCase();
}

function escHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function setButtonLoading(btn, loading, loadingText = 'Loading...') {
  if (!btn) return;
  if (loading) {
    btn._origHTML  = btn.innerHTML;
    btn.innerHTML = `<span class="btn-text">${loadingText}</span>`;
    btn.disabled  = true;
  } else {
    btn.innerHTML = btn._origHTML || btn.innerHTML;
    btn.disabled  = false;
  }
}
