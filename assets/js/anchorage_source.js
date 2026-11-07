/* ТЗ-12.3 — Kharg anchorage series + snapshots.
 * Series: Supabase table anchorage_daily (source = tz12_frame_v2, new pipeline).
 * Fallback: /data/anchorage.json — DEPRECATED (old ship_backfill.py series, frozen
 * as a reference; kept only so the page still renders if Supabase is unreachable).
 * Snapshots: Supabase Storage bucket anchorage_snapshots/{previous,latest}.png
 * described by snapshots.json; rendered into any element with id="aq-snaps".
 */
(function () {
  var SB_URL = 'https://yoekhkczwdeyauwarsyy.supabase.co';
  var SB_KEY = 'sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM';
  var H = { apikey: SB_KEY, Authorization: 'Bearer ' + SB_KEY };
  var SNAP = SB_URL + '/storage/v1/object/public/anchorage_snapshots/';

  // Returns {updated, records:[{date,vlcc,suezmax,aframax,mr,total}], source}
  // total = large vessels without small (<100 m), same meaning as the old series.
  window.loadAnchorageSeries = async function () {
    try {
      var r = await fetch(SB_URL + '/rest/v1/anchorage_daily?select=date,vlcc,suezmax,aframax,mr,small,total_no_small,created_at&order=date&limit=5000', { headers: H });
      if (r.ok) {
        var rows = await r.json();
        if (rows && rows.length) {
          var upd = rows.reduce(function (m, x) { return x.created_at > m ? x.created_at : m; }, '');
          return {
            source: 'supabase:anchorage_daily',
            updated: upd,
            records: rows.map(function (x) {
              return { date: x.date, vlcc: x.vlcc, suezmax: x.suezmax, aframax: x.aframax, mr: x.mr, total: x.total_no_small };
            })
          };
        }
      }
    } catch (e) { /* fall through to deprecated file */ }
    // DEPRECATED fallback — old series, not updated any more (reference only).
    var j = await (await fetch('/data/anchorage.json')).json();
    j.source = 'anchorage.json (deprecated)';
    return j;
  };

  window.renderAnchorageSnapshots = async function (el) {
    if (!el) return;
    try {
      var m = await (await fetch(SNAP + 'snapshots.json?t=' + Date.now())).json();
      if (!m || !m.latest || !m.previous) return;
      ['previous', 'latest'].forEach(function (k) {
        var img = el.querySelector('[data-snap="' + k + '"] img');
        var cap = el.querySelector('[data-snap="' + k + '"] .aq-snap-date');
        var a = el.querySelector('[data-snap="' + k + '"] a');
        var src = SNAP + m[k].file + '?v=' + m[k].date;
        if (img) { img.src = src; img.alt = 'Kharg anchorage Sentinel-1 SAR ' + m[k].date; }
        if (a) a.href = src;
        if (cap) cap.textContent = m[k].date;
      });
      el.style.display = '';
    } catch (e) { /* no snapshots yet -> block stays hidden */ }
  };

  document.addEventListener('DOMContentLoaded', function () {
    window.renderAnchorageSnapshots(document.getElementById('aq-snaps'));
  });
})();
