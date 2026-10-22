const form = document.querySelector('#listing-form');
const message = document.querySelector('#form-message');
const photoInput = document.querySelector('#photo-input');
const preview = document.querySelector('#photo-previews');
const maxPhotos = 6;
const maxFileSize = 12 * 1024 * 1024;
const plans = {
  basic: { label: 'Basic Listing', price: 2500, limit: 3 },
  pro: { label: 'Seller Pro', price: 7500, limit: 15 },
  enterprise: { label: 'Enterprise', price: null, limit: 100 }
};
let account = null;
let previewUrls = [];
let setupRequired = false;
form.elements.year.max = String(new Date().getFullYear() + 1);

function showMessage(target, text, isError = false) {
  target.textContent = text;
  target.classList.toggle('error', isError);
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
}

function priceText(price) {
  return `KES ${Number(price).toLocaleString('en-KE')}`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    ...options,
    headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) }
  });
  let result = {};
  try {
    result = await response.json();
  } catch (error) {
    throw new Error('The server returned an invalid response. Please try again.');
  }
  if (!response.ok) throw new Error(result.error || 'The request could not be completed.');
  return result;
}

function showSignIn(firstAdminSetup = setupRequired) {
  account = null;
  setupRequired = firstAdminSetup;
  document.querySelector('#account-login').hidden = false;
  document.querySelector('#login-form').hidden = firstAdminSetup;
  document.querySelector('#show-register').hidden = firstAdminSetup;
  document.querySelector('#register-form').hidden = true;
  document.querySelector('#setup-form').hidden = !firstAdminSetup;
  document.querySelector('#forgot-password-link').hidden = firstAdminSetup;
  document.querySelector('#login-intro').hidden = firstAdminSetup;
  document.querySelector('#account-login > h1').innerHTML = firstAdminSetup
    ? 'Create your<br><em>admin account.</em>'
    : 'Good to have<br><em>you back.</em>';
  document.querySelector('#registration-result').hidden = true;
  document.querySelector('#registration-result').replaceChildren();
  document.querySelector('#forgot-password').hidden = true;
  document.querySelector('#password-change').hidden = true;
  document.querySelector('#portal-content').hidden = true;
  const recoveryReveal = document.querySelector('#recovery-reveal');
  recoveryReveal.replaceChildren();
  recoveryReveal.hidden = true;
  const credentials = document.querySelector('#credential-reveal');
  credentials.replaceChildren();
  credentials.hidden = true;
  const recoveryResult = document.querySelector('#recovery-result');
  recoveryResult.replaceChildren();
  recoveryResult.hidden = true;
  document.querySelector('#header-action').textContent = 'Sign in ↗';
  document.querySelector('#header-action').href = '#account';
}

function formatPlan(tenant) {
  const plan = plans[tenant.plan];
  return plan?.price ? `${plan.label} · ${priceText(plan.price)} / month` : `${plan?.label || 'Unknown'} · Custom / month`;
}

function updateAccountHeader() {
  const admin = account.role === 'admin';
  document.querySelector('#account-eyebrow').textContent = admin ? 'AUTOQUEST ADMIN' : 'YOUR AUTOQUEST SHOWROOM';
  document.querySelector('#portal-heading').innerHTML = admin ? 'Manage <em>the marketplace.</em>' : 'Manage <em>your cars.</em>';
  document.querySelector('#portal-description').textContent = `${account.name} · ${account.email}`;
  document.querySelector('#tenant-management').hidden = !admin;
  document.querySelector('#featured-field').hidden = !admin;
  document.querySelector('#tenant-plan-card').hidden = admin;
  document.querySelector('#listing-form-section').hidden = !admin && !account.permissions.can_create_listings;
  document.querySelector('#listing-management-section').hidden = !admin && !account.permissions.can_manage_listings;
  document.querySelector('#contact-card').hidden = !admin && !account.permissions.can_manage_contact;
  document.querySelector('#header-action').textContent = 'Sign out ↗';
  document.querySelector('#header-action').href = '#sign-out';

  const notice = document.querySelector('#account-notice');
  const noticeText = document.querySelector('#account-notice-text');
  if (!admin && account.subscription_status === 'pending') {
    notice.hidden = false;
    noticeText.innerHTML = '<strong>Your seller subscription is awaiting approval.</strong> AutoQuest will confirm the selected plan before your cars are visible to buyers. Arrange payment with AutoQuest; online checkout is not connected yet.';
  } else if (!admin && account.subscription_status === 'expired') {
    notice.hidden = false;
    noticeText.innerHTML = '<strong>Your seller subscription has expired.</strong> Contact AutoQuest to renew your plan and restore your listings.';
  } else {
    notice.hidden = true;
  }

  if (!admin) {
    const plan = plans[account.plan];
    document.querySelector('#tenant-plan-name').textContent = plan.label;
    document.querySelector('#tenant-plan-status').textContent = `${account.subscription_status} subscription${account.subscription_expires ? ` · renew by ${new Date(account.subscription_expires).toLocaleDateString('en-KE')}` : ''}`;
    document.querySelector('#tenant-plan-price').textContent = plan.price ? priceText(plan.price) : 'Custom';
    const allowed = account.subscription_status === 'active' && account.permissions.can_create_listings;
    for (const field of form.elements) field.disabled = !allowed;
    if (!allowed) {
      document.querySelector('#save-listing').textContent = 'Subscription approval required';
    } else {
      document.querySelector('#save-listing').innerHTML = 'Publish listing <span aria-hidden="true">↗</span>';
    }
  }

  document.querySelector('#contact-form').elements.whatsapp.value = account.whatsapp || '';
}

function validatePhotos(files) {
  if (!files.length) return 'Choose at least one car photo.';
  if (files.length > maxPhotos) return `Choose no more than ${maxPhotos} photos.`;
  for (const file of files) {
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) return 'Use JPG, PNG or WebP image files.';
    if (file.size > maxFileSize) return 'Each photo must be smaller than 12 MB.';
  }
  return '';
}

function compressPhoto(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`Could not read ${file.name}.`));
    reader.onload = () => {
      const image = new Image();
      image.onerror = () => reject(new Error(`Could not process ${file.name}.`));
      image.onload = () => {
        const scale = Math.min(1, 1500 / Math.max(image.width, image.height));
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(image.width * scale));
        canvas.height = Math.max(1, Math.round(image.height * scale));
        const context = canvas.getContext('2d');
        if (!context) {
          reject(new Error('Your browser could not process the selected photos.'));
          return;
        }
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
        resolve(canvas.toDataURL('image/jpeg', 0.78));
      };
      image.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

function releasePreviews() {
  previewUrls.forEach(url => URL.revokeObjectURL(url));
  previewUrls = [];
  preview.replaceChildren();
}

function showRecoveryCode(code, message) {
  const reveal = document.querySelector('#recovery-reveal');
  reveal.innerHTML = `<strong>${escapeHtml(message)}</strong><p>Save this new recovery code somewhere private. It is shown once; your previous recovery code no longer works.</p><div class="credential-line"><span>ONE-TIME RECOVERY CODE</span><code>${escapeHtml(code)}</code></div>`;
  reveal.hidden = false;
  reveal.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

async function changePassword(passwordForm, status, successMessage) {
  const data = new FormData(passwordForm);
  const newPassword = data.get('newPassword');
  if (newPassword !== data.get('confirmPassword')) {
    showMessage(status, 'Your new passwords do not match.', true);
    return false;
  }
  const result = await api('/api/auth/password', {
    method: 'POST',
    body: JSON.stringify({ currentPassword: data.get('currentPassword'), newPassword })
  });
  passwordForm.reset();
  showMessage(status, successMessage);
  showRecoveryCode(result.recovery_code, 'Password updated.');
  if (account) account.must_change_password = false;
  return true;
}

photoInput.addEventListener('change', () => {
  releasePreviews();
  const files = [...photoInput.files];
  const error = validatePhotos(files);
  if (error) {
    showMessage(message, error, true);
    photoInput.setCustomValidity(error);
    return;
  }
  photoInput.setCustomValidity('');
  showMessage(message, '');
  files.forEach(file => {
    const image = document.createElement('img');
    image.className = 'photo-preview';
    image.alt = file.name;
    image.src = URL.createObjectURL(file);
    previewUrls.push(image.src);
    preview.append(image);
  });

});

form.addEventListener('submit', async event => {
  event.preventDefault();
  const files = [...photoInput.files];
  const photoError = validatePhotos(files);
  if (photoError) {
    showMessage(message, photoError, true);
    photoInput.setCustomValidity(photoError);
    photoInput.reportValidity();
    return;
  }
  const submit = document.querySelector('#save-listing');
  submit.disabled = true;
  showMessage(message, 'Preparing your photos and saving the listing…');
  try {
    const data = new FormData(form);
    const images = await Promise.all(files.map(compressPhoto));
    await api('/api/listings', {
      method: 'POST',
      body: JSON.stringify({
        title: data.get('title').trim(),
        make: data.get('make').trim(),
        model: data.get('model').trim(),
        year: Number(data.get('year')),
        price: Number(data.get('price')),
        location: data.get('location').trim(),
        mileage: Number(data.get('mileage')),
        bodyType: data.get('bodyType'),
        transmission: data.get('transmission'),
        fuel: data.get('fuel'),
        engine: data.get('engine').trim(),
        colour: data.get('colour').trim(),
        description: data.get('description').trim(),
        featured: data.get('featured') === 'on',
        images
      })
    });
    form.reset();
    releasePreviews();
    photoInput.setCustomValidity('');
    showMessage(message, 'Listing published and added to the marketplace.');
    await loadPortal();
    document.querySelector('#listing-form-section').scrollIntoView({ behavior: 'smooth' });
  } catch (error) {
    console.error('Could not publish the vehicle listing.', error);
    showMessage(message, error.message, true);
  } finally {
    submit.disabled = false;
  }
});

async function loadListings() {
  const result = await api('/api/account/listings');
  const listings = result.listings;
  document.querySelector('#listing-count').textContent = listings.filter(listing => listing.status === 'active').length;
  const container = document.querySelector('#manage-list');
  if (!listings.length) {
    container.innerHTML = '<p class="manage-empty">No listings yet. Add your first car above and it will appear here.</p>';
    return;
  }
  container.innerHTML = listings.map(listing => `
    <article class="manage-row">
      <img src="${escapeHtml(listing.images?.[0] || '')}" alt="" loading="lazy">
      <div><h3>${escapeHtml(listing.title)}</h3><p>${escapeHtml(listing.year)} · ${escapeHtml(listing.location)} · ${listing.status === 'sold' ? 'Sold' : 'Available'}</p></div>
      <span class="manage-price">${priceText(listing.price)}</span>
      ${account.role === 'admin' || account.permissions.can_manage_listings ? `<div class="manage-actions">
        <button type="button" data-action="status" data-id="${escapeHtml(listing.id)}">${listing.status === 'sold' ? 'Mark available' : 'Mark sold'}</button>
        <button type="button" class="delete-button" data-action="delete" data-id="${escapeHtml(listing.id)}">Remove</button>
      </div>` : ''}
    </article>`).join('');
}

async function loadTenants() {
  if (account.role !== 'admin') return;
  const result = await api('/api/tenants');
  const container = document.querySelector('#tenant-list');
  if (!result.tenants.length) {
    container.innerHTML = '<p class="manage-empty">No seller accounts yet. Create a tenant account above to invite your first seller.</p>';
    return;
  }
  container.innerHTML = result.tenants.map(tenant => {
    const amount = tenant.plan_price ? priceText(tenant.plan_price) : 'Custom';
    const statusLabel = tenant.subscription_status[0].toUpperCase() + tenant.subscription_status.slice(1);
    const action = tenant.subscription_status === 'active'
      ? `<button type="button" data-tenant-status="suspended" data-id="${escapeHtml(tenant.id)}">Pause access</button>`
      : `<button type="button" data-tenant-status="active" data-id="${escapeHtml(tenant.id)}">Approve / renew 30 days</button>`;
    return `<article class="tenant-row">
      <div><h3>${escapeHtml(tenant.name)}</h3><p>${escapeHtml(tenant.email)}</p></div>
      <div><strong>${escapeHtml(tenant.plan_label)}</strong><p>${escapeHtml(amount)} / month</p></div>
      <div><span class="subscription-badge ${escapeHtml(tenant.subscription_status)}">${escapeHtml(statusLabel)}</span><p>${tenant.listing_count} / ${plans[tenant.plan].limit} cars</p></div>
      <div class="tenant-actions">${action}</div>
      <form class="tenant-permission-editor" data-tenant-id="${escapeHtml(tenant.id)}">
        <strong>Permissions</strong>
        <label class="form-check"><input type="checkbox" name="can_create_listings" ${tenant.permissions.can_create_listings ? 'checked' : ''}><span>Create listings</span></label>
        <label class="form-check"><input type="checkbox" name="can_manage_listings" ${tenant.permissions.can_manage_listings ? 'checked' : ''}><span>Mark sold / remove own listings</span></label>
        <label class="form-check"><input type="checkbox" name="can_manage_contact" ${tenant.permissions.can_manage_contact ? 'checked' : ''}><span>Update WhatsApp contact</span></label>
        <button class="text-button" type="submit">Save permissions</button>
      </form>
    </article>`;
  }).join('');
}

async function loadPortal() {
  try {
    await loadListings();
    await loadTenants();
  } catch (error) {
    if (/sign in|session has expired/i.test(error.message)) {
      showSignIn();
      showMessage(document.querySelector('#login-message'), error.message, true);
      return;
    }
    showMessage(message, error.message, true);
  }
}

async function renderAccount(nextAccount) {
  account = nextAccount;
  document.querySelector('#account-login').hidden = true;
  document.querySelector('#forgot-password').hidden = true;
  document.querySelector('#portal-content').hidden = true;
  document.querySelector('#password-change').hidden = true;
  if (account.must_change_password) {
    document.querySelector('#password-change').hidden = false;
    return;
  }
  updateAccountHeader();
  document.querySelector('#portal-content').hidden = false;
  await loadPortal();
}

document.querySelector('#login-form').addEventListener('submit', async event => {
  event.preventDefault();
  const loginForm = event.currentTarget;
  const target = document.querySelector('#login-message');
  const submit = loginForm.querySelector('button');
  submit.disabled = true;
  showMessage(target, 'Signing in…');
  try {
    const data = new FormData(loginForm);
    const result = await api('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email: data.get('email'), password: data.get('password') })
    });
    loginForm.reset();
    showMessage(target, '');
    await renderAccount(result.account);
  } catch (error) {
    showMessage(target, error.message, true);
  } finally {
    submit.disabled = false;
  }
});

document.querySelector('#show-register').addEventListener('click', () => {
  document.querySelector('#login-form').hidden = true;
  document.querySelector('#show-register').hidden = true;
  document.querySelector('#register-form').hidden = false;
  document.querySelector('#login-intro').textContent = 'Create your seller login with your email and password.';
});

document.querySelector('#show-login-from-register').addEventListener('click', () => {
  document.querySelector('#register-form').hidden = true;
  document.querySelector('#login-form').hidden = false;
  document.querySelector('#show-register').hidden = false;
  document.querySelector('#login-intro').textContent = 'Sign in to your showroom, or create an account to get started.';
});

document.querySelector('#register-form').addEventListener('submit', async event => {
  event.preventDefault();
  const registrationForm = event.currentTarget;
  const target = document.querySelector('#register-message');
  const data = new FormData(registrationForm);
  if (data.get('password') !== data.get('confirmPassword')) {
    showMessage(target, 'Your passwords do not match.', true);
    return;
  }
  const submit = registrationForm.querySelector('button[type="submit"]');
  submit.disabled = true;
  try {
    const result = await api('/api/auth/register', {
      method: 'POST',
      body: JSON.stringify({
        name: data.get('name'),
        email: data.get('email'),
        password: data.get('password'),
        plan: data.get('plan')
      })
    });
    registrationForm.reset();
    showRecoveryCode(result.recovery_code, 'Seller account created.');
    await renderAccount(result.account);
  } catch (error) {
    showMessage(target, error.message, true);
  } finally {
    submit.disabled = false;
  }
});

document.querySelector('#setup-form').addEventListener('submit', async event => {
  event.preventDefault();
  const setupForm = event.currentTarget;
  const target = document.querySelector('#setup-message');
  const data = new FormData(setupForm);
  if (data.get('password') !== data.get('confirmPassword')) {
    showMessage(target, 'Your passwords do not match.', true);
    return;
  }
  const submit = setupForm.querySelector('button[type="submit"]');
  submit.disabled = true;
  try {
    const result = await api('/api/auth/setup', {
      method: 'POST',
      body: JSON.stringify({
        name: data.get('name'),
        email: data.get('email'),
        password: data.get('password')
      })
    });
    setupForm.reset();
    setupRequired = false;
    showRecoveryCode(result.recovery_code, 'Administrator account created.');
    await renderAccount(result.account);
  } catch (error) {
    showMessage(target, error.message, true);
  } finally {
    submit.disabled = false;
  }
});

document.querySelector('#password-form').addEventListener('submit', async event => {
  event.preventDefault();
  const passwordForm = event.currentTarget;
  const target = document.querySelector('#password-message');
  const submit = passwordForm.querySelector('button');
  submit.disabled = true;
  try {
    if (await changePassword(passwordForm, target, 'Your password has been changed.')) {
      await renderAccount(account);
    }
  } catch (error) {
    showMessage(target, error.message, true);
  } finally {
    submit.disabled = false;
  }
});

document.querySelector('#account-password-form').addEventListener('submit', async event => {
  event.preventDefault();
  const passwordForm = event.currentTarget;
  const target = document.querySelector('#account-password-message');
  const submit = passwordForm.querySelector('button');
  submit.disabled = true;
  try {
    await changePassword(passwordForm, target, 'Your password has been changed.');
  } catch (error) {
    showMessage(target, error.message, true);
  } finally {
    submit.disabled = false;
  }
});

document.querySelector('#forgot-password-link').addEventListener('click', () => {
  document.querySelector('#account-login').hidden = true;
  document.querySelector('#forgot-password').hidden = false;
  document.querySelector('#recovery-result').hidden = true;
});

document.querySelector('#back-to-login').addEventListener('click', () => {
  const reveal = document.querySelector('#recovery-result');
  if (reveal.hidden || window.confirm('The one-time recovery code will disappear. Have you saved it?')) {
    reveal.hidden = true;
    reveal.replaceChildren();
    document.querySelector('#forgot-password').hidden = true;
    document.querySelector('#account-login').hidden = false;
  }
});

document.querySelector('#recovery-form').addEventListener('submit', async event => {
  event.preventDefault();
  const recoveryForm = event.currentTarget;
  const target = document.querySelector('#recovery-message');
  const data = new FormData(recoveryForm);
  const newPassword = data.get('newPassword');
  if (newPassword !== data.get('confirmPassword')) {
    showMessage(target, 'Your new passwords do not match.', true);
    return;
  }
  const submit = recoveryForm.querySelector('button');
  submit.disabled = true;
  try {
    const result = await api('/api/auth/recovery', {
      method: 'POST',
      body: JSON.stringify({
        email: data.get('email'),
        recoveryCode: data.get('recoveryCode'),
        newPassword
      })
    });
    recoveryForm.reset();
    showMessage(target, 'Password reset. Save your new recovery code below before signing in.');
    const reveal = document.querySelector('#recovery-result');
    reveal.innerHTML = `<strong>Password reset successfully.</strong><p>Your previous recovery code has been replaced. Save this new one-time code in a private, secure place.</p><div class="credential-line"><span>NEW RECOVERY CODE</span><code>${escapeHtml(result.recovery_code)}</code></div>`;
    reveal.hidden = false;
  } catch (error) {
    showMessage(target, error.message, true);
  } finally {
    submit.disabled = false;
  }
});

document.querySelector('#sign-out').addEventListener('click', async event => {
  event.preventDefault();
  try {
    await api('/api/auth/logout', { method: 'POST', body: '{}' });
  } catch (error) {
    console.error('Could not sign out cleanly.', error);
  }
  showSignIn();
  window.location.hash = 'account';
});

document.querySelector('#header-action').addEventListener('click', async event => {
  if (!account || event.currentTarget.href.endsWith('#account')) return;
  event.preventDefault();
  document.querySelector('#sign-out').click();
});

document.querySelector('#manage-list').addEventListener('click', async event => {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  try {
    if (button.dataset.action === 'delete') {
      const listing = button.closest('.manage-row').querySelector('h3').textContent;
      if (!window.confirm(`Remove "${listing}" from your listings?`)) return;
      await api(`/api/listings/${encodeURIComponent(button.dataset.id)}`, { method: 'DELETE' });
    } else {
      const sold = button.textContent === 'Mark sold';
      await api(`/api/listings/${encodeURIComponent(button.dataset.id)}`, {
        method: 'PATCH',
        body: JSON.stringify({ status: sold ? 'sold' : 'active' })
      });
    }
    await loadListings();
  } catch (error) {
    showMessage(message, error.message, true);
  }
});

document.querySelector('#contact-form').addEventListener('submit', async event => {
  event.preventDefault();
  const target = document.querySelector('#contact-message');
  const value = event.currentTarget.elements.whatsapp.value.trim();
  const route = account.role === 'admin' ? '/api/settings' : '/api/account';
  try {
    await api(route, { method: 'PATCH', body: JSON.stringify({ whatsapp: value }) });
    account.whatsapp = value;
    showMessage(target, 'WhatsApp contact number saved.');
  } catch (error) {
    showMessage(target, error.message, true);
  }
});

document.querySelector('#tenant-form').addEventListener('submit', async event => {
  event.preventDefault();
  const tenantForm = event.currentTarget;
  const target = document.querySelector('#tenant-message');
  const submit = tenantForm.querySelector('button');
  submit.disabled = true;
  try {
    const data = new FormData(tenantForm);
    const result = await api('/api/tenants', {
      method: 'POST',
      body: JSON.stringify({
        name: data.get('name'),
        email: data.get('email'),
        plan: data.get('plan'),
        permissions: {
          can_create_listings: data.get('can_create_listings') === 'on',
          can_manage_listings: data.get('can_manage_listings') === 'on',
          can_manage_contact: data.get('can_manage_contact') === 'on'
        }
      })
    });
    tenantForm.reset();
    const reveal = document.querySelector('#credential-reveal');
    reveal.innerHTML = `<strong>Seller account created.</strong> Share these sign-in details privately with ${escapeHtml(result.tenant.email)}. The temporary password and recovery code are shown only once.<div class="credential-line"><span>EMAIL</span><code>${escapeHtml(result.tenant.email)}</code></div><div class="credential-line"><span>TEMPORARY PASSWORD</span><code>${escapeHtml(result.temporary_password)}</code></div><div class="credential-line"><span>RECOVERY CODE</span><code>${escapeHtml(result.recovery_code)}</code></div><button type="button" id="dismiss-credentials">Hide credentials</button>`;
    reveal.hidden = false;
    reveal.querySelector('#dismiss-credentials').addEventListener('click', () => {
      reveal.replaceChildren();
      reveal.hidden = true;
    });
    showMessage(target, '');
    await loadTenants();
  } catch (error) {
    showMessage(target, error.message, true);
  } finally {
    submit.disabled = false;
  }
});

document.querySelector('#tenant-list').addEventListener('click', async event => {
  const button = event.target.closest('button[data-tenant-status]');
  if (!button) return;
  try {
    await api(`/api/tenants/${encodeURIComponent(button.dataset.id)}`, {
      method: 'PATCH',
      body: JSON.stringify({ subscription_status: button.dataset.tenantStatus })
    });
    await loadTenants();
  } catch (error) {
    showMessage(document.querySelector('#tenant-message'), error.message, true);
  }
});

document.querySelector('#tenant-list').addEventListener('submit', async event => {
  const permissionForm = event.target.closest('.tenant-permission-editor');
  if (!permissionForm) return;
  event.preventDefault();
  const data = new FormData(permissionForm);
  const submit = permissionForm.querySelector('button');
  submit.disabled = true;
  try {
    await api(`/api/tenants/${encodeURIComponent(permissionForm.dataset.tenantId)}`, {
      method: 'PATCH',
      body: JSON.stringify({
        permissions: {
          can_create_listings: data.get('can_create_listings') === 'on',
          can_manage_listings: data.get('can_manage_listings') === 'on',
          can_manage_contact: data.get('can_manage_contact') === 'on'
        }
      })
    });
    showMessage(document.querySelector('#tenant-message'), 'Seller permissions saved.');
    await loadTenants();
  } catch (error) {
    showMessage(document.querySelector('#tenant-message'), error.message, true);
  } finally {
    submit.disabled = false;
  }
});

document.querySelectorAll('.current-year').forEach(node => node.textContent = new Date().getFullYear());

api('/api/auth/session')
  .then(({ account: current, setup_required: firstAdminSetup }) => {
    setupRequired = firstAdminSetup;
    return current ? renderAccount(current) : showSignIn(firstAdminSetup);
  })
  .catch(error => {
    console.error('Could not check the seller sign-in status.', error);
    showSignIn();
    showMessage(document.querySelector('#login-message'), 'The seller portal is unavailable. Please try again shortly.', true);
  });
