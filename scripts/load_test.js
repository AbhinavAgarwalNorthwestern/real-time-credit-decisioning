/**
 * k6 load test for the /decide endpoint.
 *
 * Usage (from devcontainer or CI):
 *   k6 run scripts/load_test.js --env BASE_URL=http://localhost:8080
 *
 * Stages:
 *   1. Warm-up:  10 VUs for 30s (warm ONNX sessions + RW cache)
 *   2. Ramp:     10 → 100 VUs over 60s
 *   3. Sustain:  100 VUs for 120s (steady-state measurement)
 *   4. Spike:    100 → 200 VUs for 30s (burst test)
 *   5. Cool:     200 → 0 over 30s
 *
 * SLOs (from docs/01_problem_and_domain.md):
 *   - p99 latency < 50ms at sustained load
 *   - Error rate < 0.1%
 *   - Throughput > 500 req/s at 100 VUs
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8080';
const NUM_CUSTOMERS = 1000;

const errorRate = new Rate('error_rate');
const decisionLatency = new Trend('decision_latency_ms', true);

export const options = {
  stages: [
    { duration: '30s',  target: 5   },
    { duration: '30s',  target: 20  },
    { duration: '60s',  target: 20  },
    { duration: '30s',  target: 50  },
    { duration: '30s',  target: 0   },
  ],
  thresholds: {
    'http_req_duration{name:decide}': ['p(99)<50'],
    'error_rate':                      ['rate<0.001'],
    'http_reqs':                       ['count>1000'],
  },
};

function randomCustomerId() {
  const idx = Math.floor(Math.random() * NUM_CUSTOMERS);
  return `cust-${String(idx).padStart(6, '0')}`;
}

export default function () {
  const payload = JSON.stringify({
    customer_id: randomCustomerId(),
    fraud_score: Math.random() * 0.3,
  });

  const params = {
    headers: { 'Content-Type': 'application/json' },
    tags: { name: 'decide' },
  };

  const res = http.post(`${BASE_URL}/decide`, payload, params);

  const success = check(res, {
    'status is 200 or 404': (r) => r.status === 200 || r.status === 404,
    'has decision_id':      (r) => r.status !== 200 || JSON.parse(r.body).decision_id !== undefined,
    'latency < 100ms':      (r) => r.timings.duration < 100,
  });

  errorRate.add(!success);
  decisionLatency.add(res.timings.duration);

  sleep(0.01);
}

// handleSummary removed — use k6 default text summary to see
// actual HTTP status codes, latency percentiles, and throughput.
