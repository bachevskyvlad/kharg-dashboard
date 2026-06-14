select
    date::date              as date,
    country_code,
    country_name,
    full_pct,
    gas_in_storage_twh,
    injection_twh,
    withdrawal_twh,
    trend
from {{ source('raw', 'lng_raw') }}
where full_pct is not null
