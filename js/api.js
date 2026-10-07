// API endpoint catalog — thin wrappers over core/api-client.
// No other module calls fetch() directly (single door into the network).
// Auth headers are attached automatically from the session store.

import { get, patch, post, request } from './core/api-client.js';

// -- catalog (cities / dates / theaters / shows / seats) --------------------
export const fetchCities = (options) => get('/cities', options);
export const fetchDates = (options) => get('/dates', options);
export const fetchTheaters = (cityId, options) => get(`/theaters?city_id=${encodeURIComponent(cityId)}`, options);
export const fetchShows = (theaterId, date, options) =>
  get(`/shows?theater_id=${encodeURIComponent(theaterId)}&date=${encodeURIComponent(date)}`, options);
export const fetchSeats = (showId, options) =>
  get(`/shows/${encodeURIComponent(showId)}/seats`, options);

// -- auth (contract unchanged) ---------------------------------------------
export const register = (payload) => post('/auth/register', payload);
export const login = (payload) => post('/auth/login', payload);
export const fetchMe = () => get('/me');
export const updateMe = (payload) => patch('/me', payload);
export const changePassword = (payload) => post('/me/password', payload);

// -- bookings ---------------------------------------------------------------
export const createHold = (payload) => post('/bookings/hold', payload);
export const confirmBooking = (reference, payment_method) =>
  post(`/bookings/${encodeURIComponent(reference)}/confirm`, { payment_method });
export const fetchBooking = (reference, options) =>
  get(`/bookings/${encodeURIComponent(reference)}`, options);
export const fetchMyBookings = () => get('/me/bookings');

// Phase 4 — payment orders are created server-side; the client only ever
// sends a booking reference (no amounts, no provider ids, no statuses).
export const createPaymentOrder = (booking_reference) =>
  post('/payments/orders', { booking_reference });
export const retryPaymentOrder = (booking_reference) =>
  post('/payments/retry', { booking_reference });
// Authoritative payment/booking status — the backend is the only source of
// truth after checkout; the browser never trusts provider callbacks alone.
export const fetchPaymentStatus = (reference, options) =>
  get(`/payments/status/${encodeURIComponent(reference)}`, options);

// -- content & forms --------------------------------------------------------
export const fetchReviews = (params, options) => {
  const q = params ? '?' + new URLSearchParams(params).toString() : '';
  return get(`/reviews${q}`, options);
};
export const submitReview = (payload) => post('/reviews', payload);
export const updateReview = (id, payload) => patch(`/reviews/${encodeURIComponent(id)}`, payload);
export const deleteReview = (id) => request(`/reviews/${encodeURIComponent(id)}`, { method: 'DELETE' });
export const likeReviewRequest = (id) => post(`/reviews/${encodeURIComponent(id)}/like`);
export const subscribeNewsletter = (payload) => post('/newsletter', payload);
export const submitContact = (payload) => post('/contact', payload);

// -- admin (RBAC-gated server-side; frontend role check is UX-only) --------
const adminQuery = (path, params) => {
  const q = params ? '?' + new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')),
  ).toString() : '';
  return get(`${path}${q}`);
};
export const adminDashboard = () => get('/admin/dashboard');
export const adminUsers = (params) => adminQuery('/admin/users', params);
export const adminUser = (id) => get(`/admin/users/${encodeURIComponent(id)}`);
export const adminDeactivateUser = (id) => post(`/admin/users/${encodeURIComponent(id)}/deactivate`, {});
export const adminActivateUser = (id) => post(`/admin/users/${encodeURIComponent(id)}/activate`, {});
export const adminSetUserRole = (id, role) => patch(`/admin/users/${encodeURIComponent(id)}`, { role });
export const adminMovies = (params) => adminQuery('/admin/movies', params);
export const adminCreateMovie = (payload) => post('/admin/movies', payload);
export const adminUpdateMovie = (id, payload) => patch(`/admin/movies/${encodeURIComponent(id)}`, payload);
export const adminCities = () => get('/admin/cities');
export const adminTheaters = (params) => adminQuery('/admin/theaters', params);
export const adminCreateTheater = (payload) => post('/admin/theaters', payload);
export const adminShows = (params) => adminQuery('/admin/shows', params);
export const adminCreateShow = (payload) => post('/admin/shows', payload);
export const adminUpdateShow = (id, payload) => patch(`/admin/shows/${encodeURIComponent(id)}`, payload);
export const adminCancelShow = (id) => post(`/admin/shows/${encodeURIComponent(id)}/cancel`, {});
export const adminBookings = (params) => adminQuery('/admin/bookings', params);
export const adminBooking = (id) => get(`/admin/bookings/${encodeURIComponent(id)}`);
export const adminPayments = (params) => adminQuery('/admin/payments', params);
export const adminPaymentAttempt = (id) => get(`/admin/payment-attempts/${encodeURIComponent(id)}`);
export const adminReviews = (params) => adminQuery('/admin/reviews', params);
export const adminDeleteReview = (id) => request(`/admin/reviews/${encodeURIComponent(id)}`, { method: 'DELETE' });
export const adminContacts = (params) => adminQuery('/admin/contact-messages', params);
export const adminMarkContact = (id, isRead) => patch(`/admin/contact-messages/${encodeURIComponent(id)}`, { is_read: isRead });
export const adminSubscribers = (params) => adminQuery('/admin/newsletter-subscribers', params);
export const adminAuditLogs = (params) => adminQuery('/admin/audit-logs', params);
