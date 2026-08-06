/**
 * invoice.js — Customer Invoice Generator: Client-Side Logic
 * =============================================================
 * Handles:
 *   - Dynamic line item management (add/remove rows)
 *   - Live GST calculation (CGST+SGST vs IGST)
 *   - AI HSN code lookup with debounce
 *   - Form validation
 *   - Invoice submission
 *   - Amount to words conversion
 *   - WhatsApp sharing
 */

/* ══════════════════════════════════════════════
   STATE
   ══════════════════════════════════════════════ */
let itemCounter = 0;
let merchantStateCode = '';
let hsnDebounceTimers = {};

/* Indian GST State Codes */
const STATE_CODES = {
  '01': 'Jammu & Kashmir', '02': 'Himachal Pradesh', '03': 'Punjab',
  '04': 'Chandigarh', '05': 'Uttarakhand', '06': 'Haryana',
  '07': 'Delhi', '08': 'Rajasthan', '09': 'Uttar Pradesh',
  '10': 'Bihar', '11': 'Sikkim', '12': 'Arunachal Pradesh',
  '13': 'Nagaland', '14': 'Manipur', '15': 'Mizoram',
  '16': 'Tripura', '17': 'Meghalaya', '18': 'Assam',
  '19': 'West Bengal', '20': 'Jharkhand', '21': 'Odisha',
  '22': 'Chhattisgarh', '23': 'Madhya Pradesh', '24': 'Gujarat',
  '25': 'Daman & Diu', '26': 'Dadra & Nagar Haveli',
  '27': 'Maharashtra', '29': 'Karnataka', '30': 'Goa',
  '32': 'Kerala', '33': 'Tamil Nadu', '34': 'Puducherry',
  '35': 'Andaman & Nicobar', '36': 'Telangana',
  '37': 'Andhra Pradesh', '38': 'Ladakh'
};


/* ══════════════════════════════════════════════
   INITIALIZATION
   ══════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  loadMerchantProfile();
  loadNextInvoiceNumber();
  setDefaultDate();
  addLineItem(); // Start with one empty row

  // Listen for customer GSTIN changes to determine CGST/SGST vs IGST
  const custGstin = document.getElementById('customer-gstin');
  if (custGstin) {
    custGstin.addEventListener('input', () => {
      recalculateAllRows();
    });
  }

  // Listen for customer state changes
  const custState = document.getElementById('customer-state');
  if (custState) {
    custState.addEventListener('change', () => {
      recalculateAllRows();
    });
  }

  // Listen for merchant GSTIN changes
  const mercGstin = document.getElementById('merchant-gstin');
  if (mercGstin) {
    mercGstin.addEventListener('input', () => {
      const val = mercGstin.value.trim().toUpperCase();
      if (val.length >= 2) {
        merchantStateCode = val.substring(0, 2);
      }
      recalculateAllRows();
    });
  }
});


/* ══════════════════════════════════════════════
   MERCHANT PROFILE
   ══════════════════════════════════════════════ */
async function loadMerchantProfile() {
  const phone = getPhone();
  if (!phone) return;

  try {
    const res = await fetch(`/api/merchant-profile?phone=${encodeURIComponent(phone)}`);
    const data = await res.json();
    if (!data.success || !data.profile) return;

    const p = data.profile;
    setVal('merchant-name', p.business_name || p.name || '');
    setVal('merchant-gstin', p.business_gstin || p.gstin || '');
    setVal('merchant-address', p.business_address || '');
    setVal('merchant-state', p.business_state || '');
    setVal('merchant-phone', p.business_phone || p.whatsapp_number || '');
    setVal('merchant-email', p.business_email || '');

    // Extract merchant state code from GSTIN
    const gstin = p.business_gstin || p.gstin || '';
    if (gstin && gstin.length >= 2) {
      merchantStateCode = gstin.substring(0, 2);
    }
  } catch (e) {
    console.warn('[Profile] Load error:', e);
  }
}


/* ══════════════════════════════════════════════
   INVOICE NUMBER
   ══════════════════════════════════════════════ */
async function loadNextInvoiceNumber() {
  const phone = getPhone();
  if (!phone) return;

  try {
    const res = await fetch(`/api/next-invoice-number?phone=${encodeURIComponent(phone)}`);
    const data = await res.json();
    if (data.success) {
      setVal('invoice-number', data.invoice_number);
    }
  } catch (e) {
    console.warn('[InvNum] Load error:', e);
  }
}

function setDefaultDate() {
  const el = document.getElementById('invoice-date');
  if (el && !el.value) {
    el.value = new Date().toISOString().split('T')[0];
  }
}


/* ══════════════════════════════════════════════
   LINE ITEMS: ADD / REMOVE
   ══════════════════════════════════════════════ */
function addLineItem() {
  const idx = itemCounter++;
  const tbody = document.getElementById('items-tbody');
  if (!tbody) return;

  const tr = document.createElement('tr');
  tr.id = `item-row-${idx}`;
  tr.style.animation = 'fadeIn 0.3s ease';

  tr.innerHTML = `
    <td style="font-weight:700;color:var(--gray-400);text-align:center;">
      <span class="row-number">${tbody.children.length + 1}</span>
    </td>
    <td style="min-width:180px;position:relative;">
      <input type="text" class="item-input text-left" id="item-name-${idx}"
        placeholder="Enter item name" oninput="onItemNameChange(${idx})" />
      <div class="hsn-loading" id="hsn-loading-${idx}"></div>
    </td>
    <td style="min-width:90px;">
      <input type="text" class="item-input" id="hsn-${idx}" placeholder="HSN" />
    </td>
    <td style="min-width:70px;">
      <input type="text" class="item-input" id="unit-${idx}" placeholder="NOS" value="NOS" />
    </td>
    <td style="min-width:70px;">
      <input type="number" class="item-input" id="qty-${idx}" placeholder="0"
        min="0" step="any" oninput="calculateRow(${idx})" />
    </td>
    <td style="min-width:90px;">
      <input type="number" class="item-input" id="rate-${idx}" placeholder="0.00"
        min="0" step="any" oninput="calculateRow(${idx})" />
    </td>
    <td style="min-width:80px;">
      <select class="item-input" id="gst-rate-${idx}" onchange="calculateRow(${idx})">
        <option value="0">0%</option>
        <option value="5">5%</option>
        <option value="12">12%</option>
        <option value="18" selected>18%</option>
        <option value="28">28%</option>
      </select>
    </td>
    <td style="min-width:100px;">
      <input type="text" class="item-input amount-cell" id="taxable-${idx}" readonly value="0.00" />
    </td>
    <td style="min-width:80px;">
      <input type="text" class="item-input amount-cell" id="cgst-${idx}" readonly value="0.00" />
    </td>
    <td style="min-width:80px;">
      <input type="text" class="item-input amount-cell" id="sgst-${idx}" readonly value="0.00" />
    </td>
    <td style="min-width:80px;">
      <input type="text" class="item-input amount-cell" id="igst-${idx}" readonly value="0.00" />
    </td>
    <td style="min-width:100px;">
      <input type="text" class="item-input amount-cell" id="total-${idx}" readonly value="0.00"
        style="font-weight:800;color:var(--brand-blue);" />
    </td>
    <td>
      <button type="button" class="btn-delete-row" onclick="removeLineItem(${idx})" title="Remove item">
        <svg width="18" height="18" viewBox="0 0 20 20" fill="none">
          <path d="M5 5L15 15M15 5L5 15" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
      </button>
    </td>
  `;

  tbody.appendChild(tr);
  updateRowNumbers();

  // Focus the item name field
  setTimeout(() => {
    const input = document.getElementById(`item-name-${idx}`);
    if (input) input.focus();
  }, 100);
}

function removeLineItem(idx) {
  const row = document.getElementById(`item-row-${idx}`);
  if (!row) return;

  // Don't allow removing the last item
  const tbody = document.getElementById('items-tbody');
  if (tbody && tbody.children.length <= 1) {
    showToast('At least one item is required', 'error');
    return;
  }

  row.style.animation = 'fadeOut 0.2s ease';
  setTimeout(() => {
    row.remove();
    updateRowNumbers();
    calculateTotals();
  }, 200);
}

function updateRowNumbers() {
  const tbody = document.getElementById('items-tbody');
  if (!tbody) return;
  const rows = tbody.querySelectorAll('tr');
  rows.forEach((row, i) => {
    const numEl = row.querySelector('.row-number');
    if (numEl) numEl.textContent = i + 1;
  });
}


/* ══════════════════════════════════════════════
   CALCULATIONS
   ══════════════════════════════════════════════ */
function getGSTType() {
  /**
   * Determine GST type: 'intra' (CGST+SGST) or 'inter' (IGST).
   * Based on comparing merchant state code with customer state code.
   */
  const custGstin = getVal('customer-gstin').toUpperCase();
  const custState = getVal('customer-state');

  let customerStateCode = '';

  // Priority 1: Extract from GSTIN
  if (custGstin && custGstin.length >= 2) {
    customerStateCode = custGstin.substring(0, 2);
  }
  // Priority 2: Use state dropdown (map state name to code)
  else if (custState) {
    for (const [code, name] of Object.entries(STATE_CODES)) {
      if (name === custState) {
        customerStateCode = code;
        break;
      }
    }
  }

  // If we can't determine customer state, default to intra-state
  if (!merchantStateCode || !customerStateCode) return 'intra';

  return merchantStateCode === customerStateCode ? 'intra' : 'inter';
}

function calculateRow(idx) {
  const qty = parseFloat(document.getElementById(`qty-${idx}`)?.value) || 0;
  const rate = parseFloat(document.getElementById(`rate-${idx}`)?.value) || 0;
  const gstRate = parseFloat(document.getElementById(`gst-rate-${idx}`)?.value) || 0;

  const taxable = qty * rate;
  const gstType = getGSTType();

  let cgst = 0, sgst = 0, igst = 0;
  if (gstType === 'intra') {
    cgst = taxable * (gstRate / 2) / 100;
    sgst = taxable * (gstRate / 2) / 100;
  } else {
    igst = taxable * gstRate / 100;
  }

  const total = taxable + cgst + sgst + igst;

  setVal(`taxable-${idx}`, taxable.toFixed(2));
  setVal(`cgst-${idx}`, cgst.toFixed(2));
  setVal(`sgst-${idx}`, sgst.toFixed(2));
  setVal(`igst-${idx}`, igst.toFixed(2));
  setVal(`total-${idx}`, total.toFixed(2));

  calculateTotals();
}

function recalculateAllRows() {
  const tbody = document.getElementById('items-tbody');
  if (!tbody) return;
  const rows = tbody.querySelectorAll('tr');
  rows.forEach(row => {
    const idMatch = row.id.match(/item-row-(\d+)/);
    if (idMatch) calculateRow(parseInt(idMatch[1]));
  });
}

function calculateTotals() {
  const tbody = document.getElementById('items-tbody');
  if (!tbody) return;

  let subtotal = 0, totalCGST = 0, totalSGST = 0, totalIGST = 0;

  const rows = tbody.querySelectorAll('tr');
  rows.forEach(row => {
    const idMatch = row.id.match(/item-row-(\d+)/);
    if (!idMatch) return;
    const idx = idMatch[1];

    subtotal += parseFloat(document.getElementById(`taxable-${idx}`)?.value) || 0;
    totalCGST += parseFloat(document.getElementById(`cgst-${idx}`)?.value) || 0;
    totalSGST += parseFloat(document.getElementById(`sgst-${idx}`)?.value) || 0;
    totalIGST += parseFloat(document.getElementById(`igst-${idx}`)?.value) || 0;
  });

  const grandTotal = subtotal + totalCGST + totalSGST + totalIGST;

  // Update summary display
  setText('summary-subtotal', fmtINR(subtotal));
  setText('summary-cgst', fmtINR(totalCGST));
  setText('summary-sgst', fmtINR(totalSGST));
  setText('summary-igst', fmtINR(totalIGST));
  setText('summary-grand-total', fmtINR(grandTotal));

  // Show/hide CGST+SGST or IGST rows based on GST type
  const gstType = getGSTType();
  const cgstRow = document.getElementById('summary-cgst-row');
  const sgstRow = document.getElementById('summary-sgst-row');
  const igstRow = document.getElementById('summary-igst-row');
  if (cgstRow) cgstRow.style.display = gstType === 'intra' ? 'flex' : 'none';
  if (sgstRow) sgstRow.style.display = gstType === 'intra' ? 'flex' : 'none';
  if (igstRow) igstRow.style.display = gstType === 'inter' ? 'flex' : 'none';

  // Amount in words
  setText('summary-words', numberToWordsIndian(grandTotal));
}


/* ══════════════════════════════════════════════
   HSN LOOKUP (AI-powered with debounce)
   ══════════════════════════════════════════════ */
function onItemNameChange(idx) {
  clearTimeout(hsnDebounceTimers[idx]);
  const itemName = document.getElementById(`item-name-${idx}`)?.value.trim();

  if (!itemName || itemName.length < 3) return;

  // Show loading spinner
  const loader = document.getElementById(`hsn-loading-${idx}`);
  if (loader) loader.style.display = 'block';

  hsnDebounceTimers[idx] = setTimeout(async () => {
    try {
      const res = await fetch('/api/hsn-lookup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ item_name: itemName })
      });
      const data = await res.json();

      if (data.success) {
        setVal(`hsn-${idx}`, data.hsn_code || '');
        const gstSelect = document.getElementById(`gst-rate-${idx}`);
        if (gstSelect) {
          const rate = String(Math.round(data.gst_rate || 18));
          if ([...gstSelect.options].some(o => o.value === rate)) {
            gstSelect.value = rate;
          }
        }
        setVal(`unit-${idx}`, data.unit || 'NOS');
        calculateRow(idx);

        const badge = data.source === 'cache' ? '(cached)' : '(AI)';
        showToast(`HSN ${data.hsn_code} found ${badge}`, 'info');
      }
    } catch (e) {
      console.warn('[HSN] Lookup error:', e);
    } finally {
      if (loader) loader.style.display = 'none';
    }
  }, 700); // 700ms debounce
}


/* ══════════════════════════════════════════════
   AMOUNT TO WORDS (Indian English)
   ══════════════════════════════════════════════ */
function numberToWordsIndian(amount) {
  if (!amount || amount === 0) return 'Zero Rupees Only';

  const ones = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven',
    'Eight', 'Nine', 'Ten', 'Eleven', 'Twelve', 'Thirteen',
    'Fourteen', 'Fifteen', 'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen'];
  const tens = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty',
    'Sixty', 'Seventy', 'Eighty', 'Ninety'];

  function _words(n) {
    if (n === 0) return '';
    if (n < 20) return ones[n];
    if (n < 100) return tens[Math.floor(n / 10)] + (n % 10 ? ' ' + ones[n % 10] : '');
    if (n < 1000) return ones[Math.floor(n / 100)] + ' Hundred' + (n % 100 ? ' ' + _words(n % 100) : '');
    if (n < 100000) return _words(Math.floor(n / 1000)) + ' Thousand' + (n % 1000 ? ' ' + _words(n % 1000) : '');
    if (n < 10000000) return _words(Math.floor(n / 100000)) + ' Lakh' + (n % 100000 ? ' ' + _words(n % 100000) : '');
    return _words(Math.floor(n / 10000000)) + ' Crore' + (n % 10000000 ? ' ' + _words(n % 10000000) : '');
  }

  const rupees = Math.floor(amount);
  const paise = Math.round((amount - rupees) * 100);

  let result = 'Rupees ' + _words(rupees);
  if (paise > 0) result += ' and ' + _words(paise) + ' Paise';
  result += ' Only';
  return result;
}


/* ══════════════════════════════════════════════
   FORM VALIDATION
   ══════════════════════════════════════════════ */
function validateForm() {
  const errors = [];

  // Customer name
  if (!getVal('customer-name').trim()) {
    errors.push('Customer Name is required');
  }

  // Customer GSTIN validation (if provided)
  const custGstin = getVal('customer-gstin').trim().toUpperCase();
  if (custGstin) {
    const gstinPattern = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/;
    if (!gstinPattern.test(custGstin)) {
      errors.push('Invalid Customer GSTIN format');
    }
  }

  // Invoice number
  if (!getVal('invoice-number').trim()) {
    errors.push('Invoice Number is required');
  }

  // Line items validation
  const tbody = document.getElementById('items-tbody');
  const rows = tbody ? tbody.querySelectorAll('tr') : [];
  if (rows.length === 0) {
    errors.push('At least one line item is required');
  }

  let hasValidItem = false;
  rows.forEach((row, i) => {
    const idMatch = row.id.match(/item-row-(\d+)/);
    if (!idMatch) return;
    const idx = idMatch[1];

    const itemName = getVal(`item-name-${idx}`).trim();
    const qty = parseFloat(getVal(`qty-${idx}`)) || 0;
    const rate = parseFloat(getVal(`rate-${idx}`)) || 0;

    if (itemName && qty > 0 && rate > 0) {
      hasValidItem = true;
    } else if (itemName || qty || rate) {
      // Partially filled row
      if (!itemName) errors.push(`Row ${i + 1}: Item Name is required`);
      if (qty <= 0) errors.push(`Row ${i + 1}: Quantity must be positive`);
      if (rate <= 0) errors.push(`Row ${i + 1}: Rate must be positive`);
    }
  });

  if (!hasValidItem) {
    errors.push('At least one complete line item is required');
  }

  return errors;
}


/* ══════════════════════════════════════════════
   FORM SUBMISSION
   ══════════════════════════════════════════════ */
async function submitInvoice() {
  // Validate
  const errors = validateForm();
  if (errors.length > 0) {
    showToast(errors[0], 'error');
    return;
  }

  // Show loading
  const overlay = document.getElementById('loading-overlay');
  if (overlay) overlay.classList.add('visible');

  const btn = document.getElementById('btn-submit');
  if (btn) { btn.disabled = true; btn.textContent = 'Generating...'; }

  try {
    // Collect line items
    const lineItems = [];
    const tbody = document.getElementById('items-tbody');
    const rows = tbody.querySelectorAll('tr');

    rows.forEach(row => {
      const idMatch = row.id.match(/item-row-(\d+)/);
      if (!idMatch) return;
      const idx = idMatch[1];

      const itemName = getVal(`item-name-${idx}`).trim();
      const qty = parseFloat(getVal(`qty-${idx}`)) || 0;
      const rate = parseFloat(getVal(`rate-${idx}`)) || 0;

      if (!itemName || qty <= 0 || rate <= 0) return; // Skip empty rows

      lineItems.push({
        item_name: itemName,
        hsn_code: getVal(`hsn-${idx}`).trim(),
        unit: getVal(`unit-${idx}`).trim() || 'NOS',
        quantity: qty,
        rate: rate,
        gst_rate: parseFloat(getVal(`gst-rate-${idx}`)) || 0,
        taxable_amount: parseFloat(getVal(`taxable-${idx}`)) || 0,
        cgst: parseFloat(getVal(`cgst-${idx}`)) || 0,
        sgst: parseFloat(getVal(`sgst-${idx}`)) || 0,
        igst: parseFloat(getVal(`igst-${idx}`)) || 0,
        total: parseFloat(getVal(`total-${idx}`)) || 0,
      });
    });

    // Calculate totals
    let subtotal = 0, totalCGST = 0, totalSGST = 0, totalIGST = 0;
    lineItems.forEach(item => {
      subtotal += item.taxable_amount;
      totalCGST += item.cgst;
      totalSGST += item.sgst;
      totalIGST += item.igst;
    });
    const grandTotal = subtotal + totalCGST + totalSGST + totalIGST;

    const payload = {
      phone: getPhone(),
      // Merchant details
      merchant_name: getVal('merchant-name').trim(),
      merchant_gstin: getVal('merchant-gstin').trim().toUpperCase(),
      merchant_address: getVal('merchant-address').trim(),
      merchant_state: getVal('merchant-state'),
      merchant_phone: getVal('merchant-phone').trim(),
      merchant_email: getVal('merchant-email').trim(),
      // Customer details
      customer_name: getVal('customer-name').trim(),
      customer_gstin: getVal('customer-gstin').trim().toUpperCase(),
      customer_phone: getVal('customer-phone').trim(),
      customer_email: getVal('customer-email').trim(),
      customer_address: getVal('customer-address').trim(),
      customer_state: getVal('customer-state'),
      // Invoice details
      invoice_number: getVal('invoice-number').trim(),
      invoice_date: getVal('invoice-date'),
      // Financial data
      line_items: lineItems,
      subtotal: subtotal,
      cgst: totalCGST,
      sgst: totalSGST,
      igst: totalIGST,
      total_amount: grandTotal,
      amount_in_words: numberToWordsIndian(grandTotal),
    };

    const res = await fetch('/api/invoice', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    const data = await res.json();

    if (!data.success) {
      const errMsg = data.errors ? data.errors.join(', ') : (data.error || 'Failed to create invoice');
      showToast(errMsg, 'error');
      return;
    }

    // Success! Show modal
    showSuccessModal({
      invoiceNumber: data.invoice_number,
      customerName: payload.customer_name,
      customerPhone: payload.customer_phone,
      grandTotal: grandTotal,
      pdfUrl: data.pdf_url,
      billId: data.bill_id,
      merchantName: payload.merchant_name,
    });

    showToast(`Invoice ${data.invoice_number} created! ✓`, 'success');

  } catch (err) {
    console.error('[Submit] Error:', err);
    showToast('Network error — is the server running?', 'error');
  } finally {
    if (overlay) overlay.classList.remove('visible');
    if (btn) { btn.disabled = false; btn.textContent = '📄 Generate Invoice'; }
  }
}


/* ══════════════════════════════════════════════
   SUCCESS MODAL
   ══════════════════════════════════════════════ */
function showSuccessModal(info) {
  const modal = document.getElementById('success-modal');
  if (!modal) return;

  setText('modal-invoice-number', info.invoiceNumber);
  setText('modal-customer-name', info.customerName);
  setText('modal-grand-total', fmtINR(info.grandTotal));

  // Set PDF download link
  const pdfBtn = document.getElementById('modal-btn-pdf');
  if (pdfBtn && info.pdfUrl) {
    pdfBtn.onclick = () => window.open(info.pdfUrl, '_blank');
  } else if (pdfBtn && info.billId) {
    pdfBtn.onclick = () => window.open(`/invoice/pdf/${info.billId}?phone=${encodeURIComponent(getPhone())}`, '_blank');
  }

  // Set WhatsApp button
  const waBtn = document.getElementById('modal-btn-whatsapp');
  if (waBtn && info.customerPhone) {
    const phone = info.customerPhone.replace(/[^0-9]/g, '');
    const waPhone = phone.startsWith('91') ? phone : '91' + phone;
    const msg = encodeURIComponent(
      `Hello ${info.customerName},\n\n` +
      `Thank you for your purchase.\n\n` +
      `Please find your GST Invoice attached.\n` +
      `Invoice No: ${info.invoiceNumber}\n` +
      `Amount: ${fmtINR(info.grandTotal)}\n\n` +
      `Regards,\n${info.merchantName}`
    );
    waBtn.onclick = () => window.open(`https://wa.me/${waPhone}?text=${msg}`, '_blank');
    waBtn.style.display = 'inline-flex';
  } else if (waBtn) {
    waBtn.style.display = 'none';
  }

  // Set "View Invoice" button
  const viewBtn = document.getElementById('modal-btn-view');
  if (viewBtn && info.billId) {
    viewBtn.onclick = () => window.location.href = `/invoice/${info.billId}`;
  }

  modal.classList.add('visible');
}

function closeSuccessModal() {
  const modal = document.getElementById('success-modal');
  if (modal) modal.classList.remove('visible');
}

function createNewInvoice() {
  closeSuccessModal();
  window.location.reload();
}


/* ══════════════════════════════════════════════
   UTILITIES
   ══════════════════════════════════════════════ */
function getVal(id) {
  const el = document.getElementById(id);
  return el ? el.value : '';
}

function setVal(id, val) {
  const el = document.getElementById(id);
  if (el) el.value = val;
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

/* Fade animations (CSS) */
const style = document.createElement('style');
style.textContent = `
  @keyframes fadeIn { from { opacity: 0; transform: translateY(-8px); } to { opacity: 1; transform: translateY(0); } }
  @keyframes fadeOut { from { opacity: 1; } to { opacity: 0; transform: translateX(30px); } }
`;
document.head.appendChild(style);
