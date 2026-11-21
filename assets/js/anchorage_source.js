/* ТЗ-12.3 — Kharg anchorage series.
 * Primary source: /data/anchorage.json (anchorage_queue, new 3-class schema:
 *   records: [{date, vlcc, suezaframax, small, arrived, departed, total}]
 *   tracks:  [{track_id, class, lat, lon, first_date, last_date, length_median}]
 * Snapshots: rendered by renderAqSnapshots() in dashboard.html using tracks[].
 */
(function () {
  window.loadAnchorageSeries = async function () {
    var j = await (await fetch('/data/anchorage.json?v=' + Date.now())).json();
    j.source = 'anchorage.json';
    return j;
  };
})();
