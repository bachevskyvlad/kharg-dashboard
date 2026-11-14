export async function tgNotify(text, env) {
  const token = env?.TG_BOT;
  const chat  = env?.TG_CHAT;
  if (!token || !chat) return;
  await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: chat, text })
  }).catch(() => {});
}

export function tgTime() {
  return new Date().toISOString().slice(0, 16).replace('T', ' ') + ' UTC';
}
