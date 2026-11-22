/* ТЗ-12.3 — Kharg anchorage series + snapshots.
 * Series: /data/anchorage.json (3-class schema: vlcc, suezaframax, small).
 * Snapshots: Supabase Storage bucket anchorage_snapshots/{previous,latest}.png
 * described by snapshots.json; rendered into any element with id="aq-snaps".
 */
(function () {
  var SB_URL = 'https://yoekhkczwdeyauwarsyy.supabase.co';
  var SB_KEY = 'sb_publishable_7j07_zzdqTGM1LXf7-o9Tg_JJRbJQSM';
  var SNAP = SB_URL + '/storage/v1/object/public/anchorage_snapshots/';

  window.loadAnchorageSeries = async function () {
    var j = await (await fetch('/data/anchorage.json')).json();
    j.source = 'anchorage.json';
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
