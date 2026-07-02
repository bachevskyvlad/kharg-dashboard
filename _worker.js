import * as loginFn from './functions/auth/login.js';
import * as logoutFn from './functions/auth/logout.js';
import * as checkFn from './functions/auth/check.js';
import * as callbackFn from './functions/auth/callback.js';

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;
    const context = { request, env, ctx, params: {} };

    if (path === '/auth/login')    return loginFn.onRequest(context);
    if (path === '/auth/logout')   return logoutFn.onRequest(context);
    if (path === '/auth/check')    return checkFn.onRequest(context);
    if (path === '/auth/callback') return callbackFn.onRequest(context);

    // app.oilradar.io → serve product pages
    if (url.hostname === 'app.oilradar.io') {
      const appPath = (path === '/' || path === '') ? '/product/index.html' : path;
      const appUrl = new URL(appPath, url.origin);
      return env.ASSETS.fetch(new Request(appUrl.toString(), request));
    }

    return env.ASSETS.fetch(request);
  }
};
