const formatPrice = price => `KES ${Number(price).toLocaleString('en-KE')}`;
const safeText = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const grid = document.querySelector('#car-grid');
const emptyState = document.querySelector('#empty-state');
let cars = [];
let contactNumber = '';
let activeFilter = 'all';
let activeSearch = { query: '', location: '', price: '' };

async function loadMarketplace() {
  const [listingsResponse, settingsResponse] = await Promise.all([
    fetch('/api/listings'),
    fetch('/api/settings')
  ]);
  if (!listingsResponse.ok || !settingsResponse.ok) {
    throw new Error('The marketplace is temporarily unavailable. Please try refreshing the page.');
  }
  const { listings } = await listingsResponse.json();
  const settings = await settingsResponse.json();
  cars = listings;
  contactNumber = settings.whatsapp || '';
  renderCars();
}

function enquiryLink(car) {
  const phone = car.sellerWhatsApp || contactNumber;
  const digits = phone.replace(/\D/g, '');
  if (!digits) return 'admin.html#account';
  const text = encodeURIComponent(`Hi AutoQuest, I'm interested in the ${car.year} ${car.title} listed for ${formatPrice(car.price)} in ${car.location}.`);
  return `https://wa.me/${digits}?text=${text}`;
}

function renderCars() {
  const query = activeSearch.query.toLowerCase();
  let visibleCars = cars.filter(car => (
    activeFilter === 'all' || car.bodyType === activeFilter
  ) && (
    !query || `${car.title} ${car.make} ${car.model} ${car.location}`.toLowerCase().includes(query)
  ) && (
    !activeSearch.location || car.location.toLowerCase().includes(activeSearch.location.toLowerCase())
  ) && (
    !activeSearch.price || Number(car.price) <= Number(activeSearch.price)
  ));

  const sort = document.querySelector('#sort-order')?.value || 'featured';
  if (sort === 'price-asc') visibleCars.sort((a, b) => a.price - b.price);
  if (sort === 'price-desc') visibleCars.sort((a, b) => b.price - a.price);
  if (sort === 'mileage') visibleCars.sort((a, b) => a.mileage - b.mileage);
  if (sort === 'newest') visibleCars.sort((a, b) => b.year - a.year);
  if (sort === 'featured') visibleCars.sort((a, b) => Number(b.featured) - Number(a.featured));

  grid.innerHTML = visibleCars.map(car => `
    <article class="car-card">
      <div class="car-photo">
        <img src="${safeText(car.images?.[0] || '')}" alt="${safeText(car.title)} in ${safeText(car.location)}" loading="lazy">
        <span class="car-tag ${car.featured ? '' : 'standard'}">${car.featured ? 'AutoQuest pick' : safeText(car.bodyType)}</span>
      </div>
      <div class="car-body">
        <div class="car-meta"><span>${safeText(car.year)} &nbsp;·&nbsp; ${Number(car.mileage).toLocaleString('en-KE')} km</span><span class="car-location">⌖ ${safeText(car.location)}</span></div>
        <h3 class="car-title">${safeText(car.title)}</h3>
        <div class="car-specs"><span>${safeText(car.transmission)}</span><span>${safeText(car.fuel)}</span><span>${safeText(car.engine)}</span></div>
        <div class="car-bottom">
          <div class="car-price"><small>ASKING PRICE</small>${formatPrice(car.price)}</div>
          <a class="car-enquire" href="${enquiryLink(car)}" ${car.sellerWhatsApp || contactNumber ? 'target="_blank" rel="noopener noreferrer"' : ''}>Enquire <span>↗</span></a>
        </div>
      </div>
    </article>`).join('');

  emptyState.hidden = visibleCars.length > 0;
}

document.querySelector('#search-form')?.addEventListener('submit', event => {
  event.preventDefault();
  activeSearch = {
    query: document.querySelector('#search-query').value.trim(),
    location: document.querySelector('#search-location').value,
    price: document.querySelector('#search-price').value
  };
  renderCars();
  document.querySelector('#inventory').scrollIntoView({ behavior: 'smooth' });
});

document.querySelectorAll('.filter-pill').forEach(button => button.addEventListener('click', () => {
  activeFilter = button.dataset.filter;
  document.querySelectorAll('.filter-pill').forEach(pill => pill.classList.toggle('selected', pill === button));
  renderCars();
}));

document.querySelector('#sort-order')?.addEventListener('change', renderCars);
document.querySelector('#clear-filters')?.addEventListener('click', () => {
  activeFilter = 'all';
  activeSearch = { query: '', location: '', price: '' };
  document.querySelector('#search-form').reset();
  document.querySelectorAll('.filter-pill').forEach(pill => pill.classList.toggle('selected', pill.dataset.filter === 'all'));
  renderCars();
});

document.querySelector('#current-year')?.replaceChildren(String(new Date().getFullYear()));

loadMarketplace().catch(error => {
  console.error('Unable to load the AutoQuest marketplace.', error);
  emptyState.hidden = false;
  emptyState.querySelector('h3').textContent = 'Cars are taking a short break.';
  emptyState.querySelector('p').textContent = error.message;
  document.querySelector('#clear-filters').hidden = true;
});
