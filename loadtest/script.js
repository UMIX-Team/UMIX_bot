import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate } from 'k6/metrics';

const errorRate = new Rate('custom_errors');

export const options = {
  stages: [
    { duration: '30s', target: 1 },
    { duration: '30s', target: 5 },
    { duration: '1m',  target: 10 },
    { duration: '1m',  target: 20 },
    { duration: '1m',  target: 50 },
    { duration: '30s', target: 0 },
  ],
  thresholds: {
    'http_req_failed':  ['rate<0.02'],
    'http_req_duration': ['p(95)<1500', 'p(99)<3000'],
    'custom_errors':    ['rate<0.02'],
  },
  abortOnFail: true,
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
};

export default function () {
  const home = http.get('https://rlfleague.ru/');
  errorRate.add(home.status >= 500 || home.status === 0);
  check(home, { 'home 200': (r) => r.status === 200 });

  const reg = http.get('https://rlfleague.ru/auth/register');
  errorRate.add(reg.status >= 500 || reg.status === 0);
  check(reg, { 'reg 200': (r) => r.status === 200 });

  sleep(2);
}
