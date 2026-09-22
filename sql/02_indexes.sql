USE airport_performance;

CREATE INDEX ix_flight_analytical_airline ON Flight (is_analytical, airline_id);
CREATE INDEX ix_flight_analytical_origin  ON Flight (is_analytical, origin_airport_id);

CREATE INDEX ix_flight_service_date       ON Flight (service_date);

CREATE INDEX ix_flight_delay              ON Flight (delay_minutes);

CREATE INDEX ix_flight_ontime             ON Flight (is_on_time);

CREATE INDEX ix_weather_airport_time      ON Weather (airport_id, observed_at_utc);

CREATE INDEX ix_bp_flight                 ON BoardingPass (flight_id);
CREATE INDEX ix_bag_status                ON Baggage (bag_status);
