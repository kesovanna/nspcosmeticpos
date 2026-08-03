// Global variable to track the active category select element
let activeCategorySelect = null;
let previousCategoryValue = '';

/**
 * Handles the change event for Category select dropdowns.
 * Supports "Create New Category" option by opening a SweetAlert2 popup.
 */
function handleCategoryChange(selectElement) {
    if (!selectElement) return;
    
    if (selectElement.value === "__NEW__" || selectElement.value === "NEW_CATEGORY") {
        const previousValue = selectElement.dataset.lastValue || "";
        
        if (typeof Swal === 'undefined') {
            console.error("SweetAlert2 (Swal) is not loaded!");
            return;
        }

        Swal.fire({
            title: 'បង្កើតប្រភេទថ្មី (Create New Category)',
            input: 'text',
            inputPlaceholder: 'បញ្ចូលឈ្មោះប្រភេទ (Enter category name)',
            showCancelButton: true,
            confirmButtonText: 'OK',
            cancelButtonText: 'Cancel',
            confirmButtonColor: '#198754',
            cancelButtonColor: '#dc3545',
            keydownListenerCapture: true,
            allowEnterKey: false,
            inputValidator: (value) => {
                if (!value || !value.trim()) {
                    return 'សូមបញ្ចូលឈ្មោះប្រភេទ! (Category name cannot be empty)';
                }
            }
        }).then((result) => {
            if (result.isConfirmed) {
                const newCategoryName = result.value.trim();
                executeCategorySave(newCategoryName, selectElement);
            } else {
                selectElement.value = previousValue;
            }
        });
    } else {
        selectElement.dataset.lastValue = selectElement.value;
    }
}

/**
 * Saves a new category via API and updates all dropdowns on the page.
 */
function executeCategorySave(categoryName, activeSelect) {
    fetch('/add_category_endpoint', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ name: categoryName })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            const allSelects = document.querySelectorAll('select[name="category"], select#productCategory, select#modalCategory, select#categorySelect');
            
            allSelects.forEach(select => {
                let exists = Array.from(select.options).some(option => option.value === categoryName);
                if (!exists) {
                    const newOption = new Option(categoryName, categoryName);
                    const lastIndex = select.options.length - 1;
                    select.add(newOption, select.options[lastIndex] || null);
                }
            });

            if (activeSelect) {
                activeSelect.value = categoryName;
                activeSelect.dataset.lastValue = categoryName;
            }

            Swal.fire('ជោគជ័យ!', 'ប្រភេទថ្មីត្រូវបានរក្សាទុក។', 'success');
        } else {
            console.error("Save failed (Server returned error):", data);
            Swal.fire('Error', data.message || 'Failed to save category', 'error');
            if (activeSelect) activeSelect.value = "";
        }
    })
    .catch(err => {
        console.error("Save failed (Network or Script error):", err);
        if (typeof Swal !== 'undefined') {
            Swal.fire('Error', 'Network error or server failed to respond', 'error');
        }
        if (activeSelect) activeSelect.value = "";
    });
}

window.handleCategoryChange = handleCategoryChange;
window.executeCategorySave = executeCategorySave;

// Luxury UI Sounds
const clickSound = new Audio('https://www.soundjay.com/buttons/sounds/button-16.mp3');
const successSound = new Audio('https://www.soundjay.com/buttons/sounds/button-3.mp3');
const popSound = new Audio('https://www.soundjay.com/buttons/sounds/button-10.mp3');

try {
    clickSound.load();
    successSound.load();
    popSound.load();
} catch (e) {
    console.log("Audio preloading failed", e);
}

function playClick() {
    if (clickSound) {
        clickSound.currentTime = 0;
        clickSound.play().catch(e => console.log("Sound blocked by browser policy"));
    }
}

function playSuccess() {
    if (successSound) {
        successSound.currentTime = 0;
        successSound.play().catch(e => console.log("Sound blocked by browser policy"));
    }
}

function playPop() {
    if (popSound) {
        popSound.currentTime = 0;
        popSound.play().catch(e => console.log("Sound blocked by browser policy"));
    }
}

document.addEventListener('click', (e) => {
    const target = e.target.closest('button, a, .card, .menu-item, .btn-login, .btn-order, .btn-scan');
    if (target) {
        playClick();
    }
});

if (typeof Swal !== 'undefined') {
    const originalFire = Swal.fire;
    Swal.fire = function(...args) {
        const result = originalFire.apply(this, args);
        playPop();
        return result;
    };
}

window.playLuxuryClick = playClick;
window.playLuxurySuccess = playSuccess;
window.playLuxuryPop = playPop;

/**
 * --- SUPREME SINGLE PAGE APPLICATION (SPA) VIEW SWITCHER ---
 */
window.showView = function(viewId, element) {
    try {
        console.log('🚀 SUPREME VIEW SWITCHER RUNNING FOR:', viewId);
        
        // ១. បិទគ្រប់ View ទាំងអស់ និងកម្ចាត់កូដរំខានចាស់ៗ
        var allViews = document.querySelectorAll('.view-container');
        for (var i = 0; i < allViews.length; i++) {
            allViews[i].style.cssText = 'display: none !important; opacity: 0 !important; visibility: hidden !important;';
            allViews[i].classList.remove('active-view');
        }
        
        // ២. បើក View ដែលបានចុចដោយបង្ខំ (Force Render)
        var target = document.getElementById(viewId);
        if (target) {
            target.classList.add('active-view');
            if (viewId === 'posView') {
                target.style.cssText = 'display: flex !important; flex-direction: row !important; width: 100% !important; height: 100% !important; opacity: 1 !important; visibility: visible !important; position: relative !important; z-index: 100 !important; gap: 20px !important; background: transparent !important;';
            } else {
                // បន្ថែមពណ៌ស (#ffffff) ដើម្បីកុំឱ្យវាថ្លាមើលធ្លុះដល់ផ្ទៃខាងក្រោយ
                target.style.cssText = 'display: flex !important; flex-direction: column !important; width: 100% !important; height: 100% !important; opacity: 1 !important; visibility: visible !important; position: relative !important; z-index: 100 !important; background-color: #ffffff !important; padding: 20px !important; overflow-y: auto !important; box-sizing: border-box !important; min-height: 100vh !important;';
            }
        } else {
            console.error('រកមិនឃើញផ្ទាំង:', viewId);
        }
        
        // ៣. ដូរពណ៌ប៊ូតុង Sidebar
        var menus = document.querySelectorAll('.sidebar-menu .menu-item, .sidebar-menu button, .sidebar-menu a');
        for (var j = 0; j < menus.length; j++) {
            menus[j].classList.remove('active');
        }
        if (element) {
            element.classList.add('active');
        }
        
        // ៤. លាក់/បង្ហាញ កន្ត្រកទំនិញ
        var cart = document.getElementById('cartPanel');
        if (cart) {
            cart.style.setProperty('display', (viewId === 'posView') ? 'flex' : 'none', 'important');
        }
        
        // ៥. ទាញ Icon មកវិញ
        if (typeof lucide !== 'undefined') {
            setTimeout(lucide.createIcons, 50);
        }
        
    } catch (error) {
        console.error('VIEW SWITCH ERROR:', error);
    }
};

// Backward Compatibility Aliases
function showHomeView() {
    const homeLink = document.querySelector('.sidebar-menu .menu-item');
    window.showView('posView', homeLink);
}

function toggleSalesReportsView() {
    const reportsLink = document.getElementById('sidebarReportsBtn');
    window.showView('reportsView', reportsLink);
}

function toggleInventoryView() {
    const inventoryLink = Array.from(document.querySelectorAll('.sidebar-menu .menu-item'))
        .find(item => item.textContent.includes('ផលិតផល'));
    window.showView('inventoryView', inventoryLink);
}

window.showHomeView = showHomeView;
window.toggleSalesReportsView = toggleSalesReportsView;
window.toggleInventoryView = toggleInventoryView;

/**
 * Renders the cart items in the sidebar with index numbering.
 */
function renderCart() {
    const container = document.getElementById('cartItems');
    const totalEl = document.getElementById('totalPrice');
    const totalRielEl = document.getElementById('totalRiel');
    const discountInput = document.getElementById('discountValue');
    const rate = typeof RIEL_RATE !== 'undefined' ? RIEL_RATE : 4000;
    
    if (!container || !totalEl || !totalRielEl || !discountInput) return;

    if (typeof cart === 'undefined' || cart.length === 0) {
        container.innerHTML = '<p style="text-align: center; color: var(--text-muted); margin-top: 20px;">មិនទាន់មានទំនិញ...</p>';
        totalEl.innerText = '$0.00'; 
        totalRielEl.innerText = '៛ 0'; 
        discountInput.value = "0"; 
        return;
    }

    let subtotal = 0;
    container.innerHTML = cart.map((item, index) => {
        const itemSub = item.price * item.qty; 
        subtotal += itemSub;
        return `
            <div style="display:flex; justify-content:space-between; padding:10px 0; border-bottom:1px solid var(--border-color);">
                <div>
                    <div style="font-weight:bold; font-size:14px;">${index + 1}. ${item.name}</div>
                    <div style="display:flex; align-items:center; gap:10px; margin-top:5px;">
                        <button onclick="updateQty('${item.name}', -1)" style="width:24px; border-radius:50%; border:1px solid var(--border-color); background:transparent; color:var(--text-color); cursor:pointer;">-</button>
                        <span style="font-weight:bold;">${item.qty}</span>
                        <button onclick="updateQty('${item.name}', 1)" style="width:24px; border-radius:50%; border:1px solid var(--border-color); background:transparent; color:var(--text-color); cursor:pointer;">+</button>
                    </div>
                </div>
                <div style="text-align:right;">
                    <span style="font-weight:bold; color:#28a745;">៛ ${(itemSub * rate).toLocaleString()}</span><br>
                    <span style="font-size:11px; color:#d81b60;">$${itemSub.toFixed(2)}</span>
                    <div onclick="removeItem('${item.name}')" style="color:#d32f2f; cursor:pointer; margin-top:5px; font-size:12px;">លុប</div>
                </div>
            </div>`;
    }).join('');

    let discountVal = parseFloat(discountInput.value) || 0;
    let discType = document.getElementById('discountType')?.value || '៛';
    let discountAmount = 0;

    if (discType === '%') discountAmount = subtotal * (discountVal / 100);
    else if (discType === '៛') discountAmount = discountVal / rate;
    else discountAmount = discountVal;

    if (discountAmount > subtotal) discountAmount = subtotal; 
    
    const finalTotal = subtotal - discountAmount;
    totalEl.innerText = `$${finalTotal.toFixed(2)}`;
    totalRielEl.innerText = `៛ ${(finalTotal * rate).toLocaleString()}`;
}

window.renderCart = renderCart;

/**
 * Enhanced Checkout Function
 */
async function checkout() {
    if (typeof cart === 'undefined' || cart.length === 0) {
        if (typeof Swal !== 'undefined') {
            return Swal.fire({ 
                icon: 'error', 
                title: 'សូមជ្រើសរើសទំនិញ!', 
                timer: 1500, 
                showConfirmButton: false 
            });
        }
        return;
    }
    
    const subtotal = cart.reduce((sum, item) => sum + (item.price * item.qty), 0);
    const discountVal = parseFloat(document.getElementById('discountValue')?.value || 0);
    const discType = document.getElementById('discountType')?.value || '%';
    let discountAmount = 0;

    if (discType === '%') discountAmount = subtotal * (discountVal / 100);
    else if (discType === '៛') discountAmount = discountVal / (typeof RIEL_RATE !== 'undefined' ? RIEL_RATE : 4000);
    else discountAmount = discountVal;
    
    if (discountAmount > subtotal) discountAmount = subtotal;
    const finalTotal = subtotal - discountAmount;

    if (typeof Swal === 'undefined') return;

    const result = await Swal.fire({
        title: 'ជ្រើសរើសវិធីបង់ប្រាក់',
        html: `
            <div style="display: flex; gap: 15px; justify-content: center; padding: 10px;">
                <button id="cash-btn" class="swal2-confirm swal2-styled" style="flex: 1; height: 100px; font-size: 18px; background: #28a745; margin: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; border-radius: 15px;">
                    <i data-lucide="banknote" style="width: 32px; height: 32px;"></i>
                    ប្រាក់សុទ្ធ (Cash)
                </button>
                <button id="qr-btn" class="swal2-confirm swal2-styled" style="flex: 1; height: 100px; font-size: 18px; background: #007bff; margin: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; border-radius: 15px;">
                    <i data-lucide="qr-code" style="width: 32px; height: 32px;"></i>
                    QR Code
                </button>
            </div>
        `,
        showConfirmButton: false,
        showCancelButton: true,
        cancelButtonText: 'បោះបង់',
        didOpen: () => {
            const cashBtn = document.getElementById('cash-btn');
            const qrBtn = document.getElementById('qr-btn');
            if (cashBtn) cashBtn.addEventListener('click', () => Swal.clickConfirm());
            if (qrBtn) qrBtn.addEventListener('click', () => Swal.clickDeny());
            if (typeof lucide !== 'undefined') lucide.createIcons();
        },
        preConfirm: () => 'cash',
        preDeny: () => 'qr'
    });

    if (result.isDismissed) return;
    const paymentMethod = result.isConfirmed ? 'cash' : 'qr';

    sessionStorage.setItem('checkout_cart', JSON.stringify(cart));
    sessionStorage.setItem('checkout_total', finalTotal.toFixed(2));
    sessionStorage.setItem('checkout_discount', discountAmount.toFixed(2));

    const form = document.createElement('form');
    form.method = 'POST';
    form.action = '/invoice';

    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || 
                      document.querySelector('input[name="csrf_token"]')?.value;
    
    if (csrfToken) {
        const csrfInput = document.createElement('input');
        csrfInput.type = 'hidden';
        csrfInput.name = 'csrf_token';
        csrfInput.value = csrfToken;
        form.appendChild(csrfInput);
    }

    const fields = {
        'cart': JSON.stringify(cart),
        'total': finalTotal.toFixed(2),
        'discount': discountAmount.toFixed(2),
        'payment_method': paymentMethod
    };

    for (const [key, value] of Object.entries(fields)) {
        const input = document.createElement('input');
        input.type = 'hidden';
        input.name = key;
        input.value = value;
        form.appendChild(input);
    }

    document.body.appendChild(form);
    form.submit();
}

window.checkout = checkout;

async function fetchStaffMembers() {
    const container = document.getElementById('userList') || document.getElementById('staffTableBody');
    if (!container) return;

    try {
        const response = await fetch('/api/users');
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const result = await response.json();

        if (result.status === 'success' && Array.isArray(result.data)) {
            if (typeof renderStaffTable === 'function') {
                renderStaffTable(result.data);
            } else {
                container.innerHTML = result.data.map(user => `
                    <tr>
                        <td>${user.username}</td>
                        <td>${user.role}</td>
                    </tr>
                `).join('');
            }
        } else {
            throw new Error(result.message || 'Invalid data format received');
        }
    } catch (error) {
        console.error("Staff load error:", error);
        if (container) {
            container.innerHTML = `
                <tr>
                    <td colspan="6" class="text-center py-4 text-danger">
                        <p>បរាជ័យក្នុងការទាញយកទិន្នន័យបុគ្គលិក (Error loading users)</p>
                        <button onclick="fetchStaffMembers()" class="btn btn-sm btn-outline-primary mt-2">Retry</button>
                    </td>
                </tr>`;
        }
    }
}

window.fetchStaffMembers = fetchStaffMembers;
console.log("NSP Luxury UI Sounds & Checkout Logic Initialized");