export async function onRequest(context) {
  const ua = context.request.headers.get('User-Agent') || '';
  const isMobile = /Mobi|Android|iPhone|iPad/i.test(ua);
  const url = new URL(context.request.url);
  url.pathname = isMobile ? '/marketing/mobile-team.html' : '/marketing/team.html';
  return context.env.ASSETS.fetch(url.toString());
}
