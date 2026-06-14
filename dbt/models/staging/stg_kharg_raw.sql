select
    date::date              as date,
    tank_id,
    radius_m,
    diameter,
    h_max,
    db_mean
from {{ source('raw', 'raw_observations') }}
