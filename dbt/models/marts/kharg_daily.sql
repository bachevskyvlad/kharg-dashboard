with base as (
    select
        date,
        vol_mn_bbl
    from {{ ref('stg_kharg_daily') }}
    order by date
),

with_metrics as (
    select
        date,
        vol_mn_bbl,

        -- 5-day centered moving average
        avg(vol_mn_bbl) over (
            order by date
            rows between 2 preceding and 2 following
        ) as vol_5d_ma,

        -- day-over-day change
        vol_mn_bbl - lag(vol_mn_bbl) over (order by date) as day_change,

        -- build / drawdown signal
        case
            when vol_mn_bbl > lag(vol_mn_bbl) over (order by date) then 'BUILD'
            when vol_mn_bbl < lag(vol_mn_bbl) over (order by date) then 'DRAWDOWN'
            else 'FLAT'
        end as signal,

        -- % change vs 30 days ago
        round(
            100.0 * (vol_mn_bbl - lag(vol_mn_bbl, 30) over (order by date))
            / nullif(lag(vol_mn_bbl, 30) over (order by date), 0),
            2
        ) as pct_change_30d
    from base
)

select * from with_metrics
