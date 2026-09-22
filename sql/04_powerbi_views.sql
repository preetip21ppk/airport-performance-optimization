USE airport_performance;

CREATE OR REPLACE VIEW vw_dim_airport AS
SELECT airport_id, airport_code, airport_name, city, state,
       CONCAT(airport_code, ' - ', COALESCE(city, airport_name)) AS airport_label
FROM Airport;

CREATE OR REPLACE VIEW vw_dim_airline AS
SELECT airline_id, airline_code, airline_name
FROM Airline;

CREATE OR REPLACE VIEW vw_dim_aircraft AS
SELECT aircraft_id, tail_number, manufacturer, model, airline_id
FROM Aircraft;

CREATE OR REPLACE VIEW vw_dim_date AS
SELECT DISTINCT
       service_date              AS date_key,
       DAYNAME(service_date)     AS day_name,
       WEEKDAY(service_date) + 1 AS weekday_number,
       MONTHNAME(service_date)   AS month_name
FROM Flight;

CREATE OR REPLACE VIEW vw_dim_kpi_threshold AS
SELECT kpi_id, metric_name, threshold_minutes, target_pct
FROM KPIThreshold;

CREATE OR REPLACE VIEW vw_fact_flight AS
SELECT flight_id,
       service_date,
       airline_id,
       origin_airport_id,
       dest_airport_id,
       aircraft_id,
       flight_number,
       delay_minutes,
       is_analytical,
       is_on_time,
       is_cancelled,
       is_diverted,
       CASE
           WHEN is_cancelled = 1 THEN 'Cancelled'
           WHEN is_diverted  = 1 THEN 'Diverted'
           WHEN is_on_time   = 1 THEN 'On Time'
           WHEN is_on_time   = 0 THEN 'Delayed'
           ELSE 'Unknown'
       END AS flight_status
FROM Flight;

CREATE OR REPLACE VIEW vw_fact_delay_reason AS
SELECT flight_id, service_date, airline_id, origin_airport_id,
       'Airline operations' AS reason, carrier_delay_min AS minutes
FROM Flight WHERE is_analytical = 1 AND carrier_delay_min > 0
UNION ALL
SELECT flight_id, service_date, airline_id, origin_airport_id,
       'Previous flight late', late_aircraft_delay_min
FROM Flight WHERE is_analytical = 1 AND late_aircraft_delay_min > 0
UNION ALL
SELECT flight_id, service_date, airline_id, origin_airport_id,
       'Air traffic control', nas_delay_min
FROM Flight WHERE is_analytical = 1 AND nas_delay_min > 0
UNION ALL
SELECT flight_id, service_date, airline_id, origin_airport_id,
       'Weather', weather_delay_min
FROM Flight WHERE is_analytical = 1 AND weather_delay_min > 0
UNION ALL
SELECT flight_id, service_date, airline_id, origin_airport_id,
       'Security', security_delay_min
FROM Flight WHERE is_analytical = 1 AND security_delay_min > 0;

CREATE OR REPLACE VIEW vw_dim_airport_ranked AS
WITH per_airport AS (
    SELECT origin_airport_id AS airport_id,
           COUNT(*)              AS flights,
           AVG(delay_minutes)    AS avg_delay
    FROM Flight
    WHERE is_analytical = 1
    GROUP BY origin_airport_id
    HAVING COUNT(*) >= 3000
),
ranked AS (
    SELECT airport_id, flights, avg_delay,
           ROW_NUMBER() OVER (ORDER BY avg_delay DESC) AS delay_rank
    FROM per_airport
)
SELECT a.airport_id, a.airport_code, a.city,
       CONCAT(a.airport_code, ' - ', COALESCE(a.city, a.airport_name)) AS airport_label,
       r.flights, ROUND(r.avg_delay, 1) AS avg_delay, r.delay_rank,
       (r.delay_rank <= 10) AS is_worst_ten
FROM Airport a
JOIN ranked r ON r.airport_id = a.airport_id;

CREATE OR REPLACE VIEW vw_fact_passenger AS
SELECT bp.boarding_pass_id, bp.passenger_id, bp.flight_id, bp.seat_number,
       f.service_date, f.airline_id, f.origin_airport_id,
       f.delay_minutes, f.is_on_time
FROM BoardingPass bp
JOIN Flight f ON f.flight_id = bp.flight_id;

SHOW FULL TABLES WHERE Table_type = 'VIEW';
