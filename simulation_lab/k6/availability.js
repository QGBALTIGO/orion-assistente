// Read-only load-profile scaffold. ONLY LOCALHOST; never production or real phones.
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Rate } from 'k6/metrics';

const raw = __ENV.ORION_LAB_URL || 'http://127.0.0.1:8000';
if (!/^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?\/?$/.test(raw)) {
  throw new Error('ORION_LAB_URL must be localhost. Production writes are forbidden.');
}
const BASE = raw.replace(/\/$/, '');
export const options = {
  stages: [
    { duration: '20s', target: 10 },
    { duration: '20s', target: 50 },
    { duration: '20s', target: 100 },
    { duration: '20s', target: 500 },
    { duration: '20s', target: 0 },
  ],
  thresholds: { http_req_failed: ['rate<0.05'], http_req_duration: ['p(95)<2000'] },
};
export const healthy = new Rate('health_requests_ok');
export default function () {
  const response = http.get(BASE + '/health', { tags: { lab: 'synthetic-read-only' } });
  const ok = check(response, { 'local health responds 200': r => r.status === 200 });
  healthy.add(ok);
  sleep(0.2);
}
// This measures local health-route load, NOT booking concurrency or WhatsApp capacity.
// Booking races need isolated PostgreSQL + authenticated test tenant + explicit cleanup.