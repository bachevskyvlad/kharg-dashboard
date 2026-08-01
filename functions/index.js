export async function onRequest(context) {
  const ua = context.request.headers.get('User-Agent') || '';
  const isMobile = /Mobi|Android|iPhone|iPad/i.test(ua);
  const url = new URL(context.request.url);
  url.pathname = isMobile ? '/marketing/mobile-index.html' : '/marketing/index.html';
  return context.env.ASSETS.fetch(url.toString());
}
