USE airport_performance;

SELECT * FROM KPIThreshold;

SELECT airline_code, airline_name
FROM Airline;

SELECT flight_id, service_date, delay_minutes
FROM Flight
LIMIT 10;

SELECT flight_id, service_date, delay_minutes
FROM Flight
WHERE delay_minutes > 300
LIMIT 10;

SELECT flight_id, service_date, delay_minutes
FROM Flight
ORDER BY delay_minutes DESC
LIMIT 10;

SELECT COUNT(*) AS total_flights
FROM Flight;

SELECT COUNT(*)                       AS flights_graded,
       SUM(is_on_time)                AS on_time,
       ROUND(AVG(is_on_time) * 100, 2) AS on_time_pct
FROM Flight
WHERE is_analytical = 1;

SELECT airline_id, COUNT(*) AS flights
FROM Flight
GROUP BY airline_id
ORDER BY flights DESC;

SELECT airline_id, COUNT(*) AS flights
FROM Flight
GROUP BY airline_id
HAVING COUNT(*) > 50000;

SELECT MIN(delay_minutes) AS earliest,
       MAX(delay_minutes) AS latest,
       AVG(delay_minutes) AS average
FROM Flight
WHERE is_analytical = 1;

SELECT a.airline_name, f.delay_minutes
FROM Flight f
JOIN Airline a ON a.airline_id = f.airline_id
LIMIT 10;

SELECT a.airline_name,
       ROUND(AVG(f.is_on_time) * 100, 1) AS on_time_pct
FROM Flight f
JOIN Airline a ON a.airline_id = f.airline_id
WHERE f.is_analytical = 1
GROUP BY a.airline_name
ORDER BY on_time_pct DESC;

SELECT o.airport_code,
       COUNT(*)             AS flights,
       AVG(f.delay_minutes) AS avg_delay
FROM Flight f
JOIN Airport o ON o.airport_id = f.origin_airport_id
WHERE f.is_analytical = 1
GROUP BY o.airport_code
HAVING COUNT(*) > 3000
ORDER BY avg_delay DESC
LIMIT 10;

SELECT f.flight_id, ac.model
FROM Flight f
LEFT JOIN Aircraft ac ON ac.aircraft_id = f.aircraft_id
WHERE ac.model IS NULL
LIMIT 10;

SELECT a.airline_name, o.airport_code, f.delay_minutes
FROM Flight f
JOIN Airline a ON a.airline_id = f.airline_id
JOIN Airport o ON o.airport_id = f.origin_airport_id
ORDER BY f.delay_minutes DESC
LIMIT 10;

SELECT flight_id, delay_minutes
FROM Flight
WHERE delay_minutes > (SELECT AVG(delay_minutes) FROM Flight)
LIMIT 10;

SELECT a.airline_name,
       (SELECT MAX(f.delay_minutes)
        FROM Flight f
        WHERE f.airline_id = a.airline_id) AS worst_delay
FROM Airline a
ORDER BY worst_delay DESC;

WITH busy AS (
    SELECT origin_airport_id, COUNT(*) AS flights
    FROM Flight
    GROUP BY origin_airport_id
)
SELECT * FROM busy
WHERE flights > 40000;

WITH perf AS (
    SELECT a.airline_name,
           ROUND(AVG(f.is_on_time) * 100, 1) AS on_time_pct
    FROM Flight f
    JOIN Airline a ON a.airline_id = f.airline_id
    WHERE f.is_analytical = 1
    GROUP BY a.airline_name
)
SELECT airline_name, on_time_pct,
       RANK() OVER (ORDER BY on_time_pct DESC) AS position
FROM perf;

WITH daily AS (
    SELECT service_date,
           ROUND(AVG(is_on_time) * 100, 1) AS pct
    FROM Flight
    WHERE is_analytical = 1
    GROUP BY service_date
)
SELECT service_date, pct,
       LAG(pct) OVER (ORDER BY service_date) AS yesterday
FROM daily;
