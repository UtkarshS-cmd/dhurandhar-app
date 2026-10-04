
const API_BASE = '/api';

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {})
    }
  });
  let data = null;
  try { data = await response.json(); } catch {}
  if (!response.ok) {
    const detail = data?.detail;
    const message = typeof detail === 'string' ? detail : detail?.message || 'Request failed';
    const error = new Error(message);
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

export const fetchCities = () => request('/cities');
export const fetchDates = () => request('/dates');
export const fetchTheaters = (cityId) => request(`/theaters?city_id=${encodeURIComponent(cityId)}`);
export const fetchShows = (theaterId, date) => request(`/shows?theater_id=${encodeURIComponent(theaterId)}&date=${encodeURIComponent(date)}`);
export const fetchSeats = (showId) => request(`/shows/${encodeURIComponent(showId)}/seats`);

export const register = (payload) => request('/auth/register', {method:'POST', body:JSON.stringify(payload)});
export const login = (payload) => request('/auth/login', {method:'POST', body:JSON.stringify(payload)});

export const createHold = (payload) => request('/bookings/hold', {method:'POST', body:JSON.stringify(payload)});
export const confirmBooking = (reference, payment_method) =>
  request(`/bookings/${encodeURIComponent(reference)}/confirm`, {
    method:'POST', body:JSON.stringify({payment_method})
  });
export const fetchBooking = (reference) => request(`/bookings/${encodeURIComponent(reference)}`);
export const fetchMyBookings = (token) => request('/me/bookings', {headers:{Authorization:`Bearer ${token}`}});

export const fetchReviews = () => request('/reviews');
export const submitReview = (payload, token) => request('/reviews', {
  method:'POST', headers:{Authorization:`Bearer ${token}`}, body:JSON.stringify(payload)
});
export const likeReviewRequest = (id, token) => request(`/reviews/${encodeURIComponent(id)}/like`, {
  method:'POST', headers:{Authorization:`Bearer ${token}`}
});
export const subscribeNewsletter = (payload) => request('/newsletter', {method:'POST', body:JSON.stringify(payload)});
export const submitContact = (payload) => request('/contact', {method:'POST', body:JSON.stringify(payload)});
