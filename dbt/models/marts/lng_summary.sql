-- Latest reading per country with YoY comparison and supply signal
with latest as (
    select distinct on (country_code)
        country_code,
        country_name,
        date        as latest_date,
        full_pct,
        gas_in_storage_twh,
        injection_twh,
        withdrawal_twh,
        trend
    from {{ ref('stg_lng_raw') }}
    order by country_code, date desc
),

yoy as (
    select distinct on (l.country_code)
        l.country_code,
        l.full_pct - h.full_pct as yoy_diff_pp
    from latest l
    join {{ ref('stg_lng_raw') }} h
        on h.country_code = l.country_code
        and h.date between l.latest_date - interval '370 days'
                       and l.latest_date - interval '355 days'
    order by l.country_code, h.date desc
)

select
    l.country_code,
    l.country_name,
    l.latest_date,
    l.full_pct,
    l.gas_in_storage_twh,
    l.injection_twh,
    l.withdrawal_twh,
    l.trend,
    round(y.yoy_diff_pp::numeric, 2)  as yoy_diff_pp,
    case
        when l.full_pct > 85 and coalesce(y.yoy_diff_pp, 0) >= 0 then 'OVERSUPPLIED'
        when l.full_pct > 70                                       then 'COMFORTABLE'
        when l.full_pct > 50 and coalesce(y.yoy_diff_pp, 0) >= 0 then 'NEUTRAL'
        when l.full_pct > 50                                       then 'WATCH'
        when l.full_pct > 30                                       then 'TIGHT'
        else 'CRITICAL'
    end as signal
from latest l
left join yoy y using (country_code)
order by l.full_pct desc nulls last
