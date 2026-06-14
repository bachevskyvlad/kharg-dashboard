-- Streak analysis: how many consecutive BUILD or DRAWDOWN days
with daily as (
    select
        date,
        vol_mn_bbl,
        day_change,
        signal
    from {{ ref('kharg_daily') }}
    where signal in ('BUILD', 'DRAWDOWN')
),

-- assign a group number that increments whenever the signal changes
grouped as (
    select
        date,
        vol_mn_bbl,
        day_change,
        signal,
        sum(
            case when signal != lag(signal) over (order by date)
                      or lag(signal) over (order by date) is null
                 then 1 else 0
            end
        ) over (order by date) as grp
    from daily
),

streaks as (
    select
        date,
        vol_mn_bbl,
        day_change,
        signal,
        grp,
        count(*)    over (partition by grp)              as streak_total_days,
        row_number() over (partition by grp order by date) as day_in_streak,
        sum(day_change) over (partition by grp)          as streak_total_change_mn_bbl,
        min(date)   over (partition by grp)              as streak_start_date
    from grouped
)

select
    date,
    vol_mn_bbl,
    day_change,
    signal,
    streak_start_date,
    day_in_streak,
    streak_total_days,
    round(streak_total_change_mn_bbl::numeric, 3) as streak_total_change_mn_bbl
from streaks
order by date
