/**
 * data.js — GST ACT AI Dashboard: API Layer
 *
 * All data is fetched from the Flask backend which reads Supabase in real-time.
 * No mock data. Every function is a real HTTP call.
 *
 * Base URL auto-detects: same origin in production, localhost:5000 in file:// mode.
 */

// ─── Detect API base URL ─────────────────────────────────────────────────────
const API_BASE = (() => {
  if (location.protocol === 'file:') return 'http://localhost:5000';
  return '';   // same-origin when served via Flask /dashboard
})();

// ─── Session state (set after OTP verify) ───────────────────────────────────
let SESSION = {
  phone:   localStorage.getItem('gst_phone')   || '',
  token:   localStorage.getItem('gst_token')   || '',
  name:    localStorage.getItem('gst_name')    || 'Merchant',
};

function saveSession(phone, token, name = '') {
  SESSION.phone = phone;
  SESSION.token = token;
  SESSION.name  = name || SESSION.name;
  localStorage.setItem('gst_phone', phone);
  localStorage.setItem('gst_token', token);
  if (name) localStorage.setItem('gst_name', name);
}

function clearSession() {
  SESSION = { phone: '', token: '', name: 'Merchant' };
  localStorage.removeItem('gst_phone');
  localStorage.removeItem('gst_token');
  localStorage.removeItem('gst_name');
}

function isLoggedIn() {
  return !!(SESSION.phone && SESSION.token);
}

// ─── Utility: Indian ₹ format ────────────────────────────────────────────────
function fmtINR(val) {
  if (val === null || val === undefined || val === '') return '—';
  const num = parseFloat(val);
  if (isNaN(num)) return String(val);
  return '₹' + num.toLocaleString('en-IN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

// ─── Utility: Date format ────────────────────────────────────────────────────
function fmtDate(dateStr) {
  if (!dateStr) return '—';
  const d = new Date(dateStr);
  if (isNaN(d)) return dateStr;
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

// ─── Utility: Current month label ────────────────────────────────────────────
function getCurrentMonthLabel() {
  return new Date().toLocaleDateString('en-IN', { month: 'long', year: 'numeric' });
}

// ─── Utility: Current month param (YYYY-MM) ──────────────────────────────────
function getCurrentMonthParam() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

// ─── API: Send OTP ───────────────────────────────────────────────────────────
async function apiSendOtp(phone) {
  const res = await fetch(`${API_BASE}/api/send-otp`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ phone }),
  });
  return res.json();
}

// ─── API: Verify OTP ─────────────────────────────────────────────────────────
async function apiVerifyOtp(phone, otp) {
  const res = await fetch(`${API_BASE}/api/verify-otp`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ phone, otp }),
  });
  return res.json();
}

// ─── API: Get Bills ──────────────────────────────────────────────────────────
async function apiFetchBills(month = '') {
  const params = new URLSearchParams({ phone: SESSION.phone });
  if (month) params.set('month', month);
  const res = await fetch(`${API_BASE}/api/bills?${params}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ─── API: Get Single Bill ────────────────────────────────────────────────────
async function apiFetchBill(billId) {
  const params = new URLSearchParams({ phone: SESSION.phone });
  const res = await fetch(`${API_BASE}/api/bills/${billId}?${params}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ─── API: Get GST Summary + Trend ────────────────────────────────────────────
async function apiFetchSummary() {
  const params = new URLSearchParams({ phone: SESSION.phone });
  const res = await fetch(`${API_BASE}/api/summary?${params}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ─── API: WhatsApp Bot Info ───────────────────────────────────────────────────
async function apiFetchWhatsappInfo() {
  const res = await fetch(`${API_BASE}/api/whatsapp-info`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
