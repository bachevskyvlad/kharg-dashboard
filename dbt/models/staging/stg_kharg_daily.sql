select
    date::date              as date,
    vol_mn_bbl,
    aggregation,
    smoothed
from {{ source('raw', 'volume_timeseries') }}
where aggregation = 'daily'
  and smoothed = false
