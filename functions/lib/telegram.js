const TG_BOT = "7745904293:AAHV2qN_5_XNxpg6Ir3iV3XmEKpokthi308";
const TG_CHAT = "204715787";

export async function tgNotify(text) {
  await fetch(`https://api.telegram.org/bot${TG_BOT}/sendMessage`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: TG_CHAT, text })
  }).catch(() => {});
}

export function tgTime() {
  return new Date().toISOString().slice(0, 16).replace('T', ' ') + ' UTC';
}
