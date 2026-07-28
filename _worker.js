import * as loginFn from './functions/auth/login.js';
import * as logoutFn from './functions/auth/logout.js';
import * as checkFn from './functions/auth/check.js';
import * as callbackFn from './functions/auth/callback.js';
import * as sendOtpFn from './functions/auth/send-otp.js';
import * as verifyOtpFn from './functions/auth/verify-otp.js';
import * as notifySarFn from './functions/auth/notify-sar.js';
import * as apiKeyFn from './functions/api/key.js';
import * as apiTankerQueueFn from './functions/api/v1/tanker-queue.js';
import * as apiStorageDailyFn from './functions/api/v1/storage/daily.js';
import * as apiStorageWeeklyFn from './functions/api/v1/storage/weekly.js';
import * as apiStorageMetricsFn from './functions/api/v1/storage/metrics.js';
import * as apiTanksFn from './functions/api/v1/tanks.js';

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;
    const context = { request, env, ctx, params: {} };

    if (path === '/auth/login')      return loginFn.onRequest(context);
    if (path === '/auth/logout')     return logoutFn.onRequest(context);
    if (path === '/auth/check')      return checkFn.onRequest(context);
    if (path === '/auth/callback')   return callbackFn.onRequest(context);
    if (path === '/auth/send-otp')   return sendOtpFn.onRequest(context);
    if (path === '/auth/verify-otp') return verifyOtpFn.onRequest(context);
    if (path === '/auth/notify-sar') return notifySarFn.onRequest(context);

    if (path === '/api/key')                  return apiKeyFn.onRequest(context);
    if (path === '/api/v1/tanker-queue')      return apiTankerQueueFn.onRequest(context);
    if (path === '/api/v1/storage/daily')     return apiStorageDailyFn.onRequest(context);
    if (path === '/api/v1/storage/weekly')    return apiStorageWeeklyFn.onRequest(context);
    if (path === '/api/v1/storage/metrics')   return apiStorageMetricsFn.onRequest(context);
    if (path === '/api/v1/tanks')             return apiTanksFn.onRequest(context);

    // app.oilradar.io → serve product pages
    const host = request.headers.get('host') || url.hostname;
    if (host === 'app.oilradar.io' && (path === '/' || path === '')) {
      const appUrl = new URL(request.url);
      appUrl.pathname = '/product/index.html';
      return env.ASSETS.fetch(new Request(appUrl.toString()));
    }

    return env.ASSETS.fetch(request);
  }
};
