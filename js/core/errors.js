// Normalized error shape + user-facing message translation.
//
// Every network failure surfaces as an ApiError (see core/api-client.js):
//   { status, code, message, details, retryAfter }
//   status  — HTTP status (0 for network/timeout failures)
//   code    — machine hint: TIMEOUT, NETWORK, OFFLINE, PARSE, or the HTTP status as string
//   message — short, safe message extracted from the backend response
//   details — raw parsed body (for structured handling like seat conflicts)
//   retryAfter — seconds from the Retry-After header, when present
//
// `message`/`details` are NEVER rendered as HTML by callers (textContent only),
// and `friendlyMessage()` below never reveals stack traces, SQL, Python
// tracebacks, provider secrets or internal identifiers.

const NETWORK_MESSAGE = 'Unable to reach the server. Check your connection and try again.';
const OFFLINE_MESSAGE = 'You appear to be offline. Reconnect and try again.';
const TIMEOUT_MESSAGE = 'The server took too long to respond. Please try again.';
const GENERIC_SERVER_MESSAGE = 'Something went wrong on our side. Please try again.';

/** Extract a safe, human-readable message from an API error response. */
export function friendlyMessage(error) {
  if (!error) return GENERIC_SERVER_MESSAGE;
  const offline = typeof navigator !== 'undefined' && navigator.onLine === false;

  if (error.code === 'OFFLINE' || (error.code === 'NETWORK' && offline)) return OFFLINE_MESSAGE;
  switch (error.code) {
    case 'NETWORK': return NETWORK_MESSAGE;
    case 'TIMEOUT': return TIMEOUT_MESSAGE;
    case 'PARSE': return GENERIC_SERVER_MESSAGE;
  }

  switch (error.status) {
    case 401:
      return error.rawMessage || 'Your session has expired. Please sign in again.';
    case 402:
      // Backend payment-decline messages are intentionally user-facing and
      // already free of internals (booking engine detail strings).
      return error.rawMessage || 'Payment was declined. Your seats are still held — please try again.';
    case 403:
      return 'You do not have permission to do that.';
    case 404:
      return error.rawMessage || 'Not found.';
    case 409:
      // Seat conflicts carry {message, seat_ids}; the message field is a
      // curated, user-facing string from the booking engine.
      return typeof error.details === 'object' && error.details?.message
        ? error.details.message
        : (error.rawMessage || 'One or more selected seats are no longer available.');
    case 422:
      return error.rawMessage || 'Some of the details you entered are not valid. Please review the form.';
    case 429: {
      const wait = Number(error.retryAfter);
      const waitText = Number.isFinite(wait) && wait > 0
        ? ` Please wait ${Math.ceil(wait)} second${Math.ceil(wait) === 1 ? '' : 's'} and try again.`
        : ' Please wait a moment and try again.';
      return `Too many attempts.${waitText}`;
    }
    case 502:
    case 503:
    case 504:
      return error.rawMessage || 'The service is temporarily unavailable. Please try again shortly.';
    default:
      if (error.status >= 500) return GENERIC_SERVER_MESSAGE;
      return error.rawMessage || GENERIC_SERVER_MESSAGE;
  }
}
