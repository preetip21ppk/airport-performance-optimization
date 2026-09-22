CREATE DATABASE IF NOT EXISTS airport_performance
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE airport_performance;

DROP TABLE IF EXISTS Baggage;
DROP TABLE IF EXISTS BoardingPass;
DROP TABLE IF EXISTS Passenger;
DROP TABLE IF EXISTS Flight;
DROP TABLE IF EXISTS Weather;
DROP TABLE IF EXISTS Aircraft;
DROP TABLE IF EXISTS Airline;
DROP TABLE IF EXISTS Airport;
DROP TABLE IF EXISTS KPIThreshold;

CREATE TABLE Airport (
    airport_id    INT          NOT NULL,
    airport_code  CHAR(3)      NOT NULL,
    airport_name  VARCHAR(120) NULL,
    city          VARCHAR(80)  NULL,
    state         VARCHAR(40)  NULL,
    PRIMARY KEY (airport_id),
    UNIQUE KEY uq_airport_code (airport_code)
) ENGINE=InnoDB COMMENT='REAL - BTS airport lookup';

CREATE TABLE Airline (
    airline_id    INT          NOT NULL,
    airline_code  VARCHAR(4)   NOT NULL,
    airline_name  VARCHAR(120) NULL,
    PRIMARY KEY (airline_id),
    UNIQUE KEY uq_airline_code (airline_code)
) ENGINE=InnoDB COMMENT='REAL - BTS carrier lookup';

CREATE TABLE Aircraft (
    aircraft_id   INT          NOT NULL,
    tail_number   VARCHAR(10)  NOT NULL,
    manufacturer  VARCHAR(80)  NULL,
    model         VARCHAR(80)  NULL,
    airline_id    INT          NULL,
    PRIMARY KEY (aircraft_id),
    UNIQUE KEY uq_tail_number (tail_number),
    CONSTRAINT fk_aircraft_airline FOREIGN KEY (airline_id) REFERENCES Airline(airline_id)
) ENGINE=InnoDB COMMENT='REAL - FAA aircraft registry';

CREATE TABLE Weather (
    weather_id       BIGINT       NOT NULL,
    airport_id       INT          NOT NULL,
    airport_code     CHAR(3)      NOT NULL,
    observed_at_utc  DATETIME     NOT NULL,
    temperature_f    DECIMAL(6,2) NULL,
    visibility_mi    DECIMAL(6,2) NULL,
    PRIMARY KEY (weather_id),
    UNIQUE KEY uq_weather_obs (airport_code, observed_at_utc),
    CONSTRAINT fk_weather_airport FOREIGN KEY (airport_id) REFERENCES Airport(airport_id)
) ENGINE=InnoDB COMMENT='REAL - hourly airport weather observations';

CREATE TABLE Flight (
    flight_id                BIGINT     NOT NULL,

    airline_id               INT        NOT NULL,
    origin_airport_id        INT        NOT NULL,
    dest_airport_id          INT        NOT NULL,
    aircraft_id              INT        NULL,
    weather_id               BIGINT     NULL,

    flight_number            INT        NULL,
    service_date             DATE       NOT NULL,

    delay_minutes            INT        NULL,
    is_cancelled             TINYINT(1) NOT NULL DEFAULT 0,
    is_diverted              TINYINT(1) NOT NULL DEFAULT 0,
    is_analytical            TINYINT(1) NOT NULL DEFAULT 0,
    is_on_time               TINYINT(1) NULL,

    carrier_delay_min        INT        NULL,
    late_aircraft_delay_min  INT        NULL,
    nas_delay_min            INT        NULL,
    weather_delay_min        INT        NULL,
    security_delay_min       INT        NULL,

    PRIMARY KEY (flight_id),
    CONSTRAINT fk_flight_airline  FOREIGN KEY (airline_id)        REFERENCES Airline(airline_id),
    CONSTRAINT fk_flight_origin   FOREIGN KEY (origin_airport_id) REFERENCES Airport(airport_id),
    CONSTRAINT fk_flight_dest     FOREIGN KEY (dest_airport_id)   REFERENCES Airport(airport_id),
    CONSTRAINT fk_flight_aircraft FOREIGN KEY (aircraft_id)       REFERENCES Aircraft(aircraft_id),
    CONSTRAINT fk_flight_weather  FOREIGN KEY (weather_id)        REFERENCES Weather(weather_id)
) ENGINE=InnoDB COMMENT='REAL - BTS on-time performance, one row per flight';

CREATE TABLE Passenger (
    passenger_id  BIGINT      NOT NULL,
    first_name    VARCHAR(40) NULL,
    last_name     VARCHAR(40) NULL,
    PRIMARY KEY (passenger_id)
) ENGINE=InnoDB COMMENT='SYNTHETIC - real passenger records are private';

CREATE TABLE BoardingPass (
    boarding_pass_id  BIGINT     NOT NULL,
    passenger_id      BIGINT     NOT NULL,
    flight_id         BIGINT     NOT NULL,
    seat_number       VARCHAR(5) NULL,
    PRIMARY KEY (boarding_pass_id),
    UNIQUE KEY uq_passenger_flight (passenger_id, flight_id),
    CONSTRAINT fk_bp_passenger FOREIGN KEY (passenger_id) REFERENCES Passenger(passenger_id),
    CONSTRAINT fk_bp_flight    FOREIGN KEY (flight_id)    REFERENCES Flight(flight_id)
) ENGINE=InnoDB COMMENT='SYNTHETIC - links passengers to real flights';

CREATE TABLE Baggage (
    bag_id        BIGINT      NOT NULL,
    passenger_id  BIGINT      NOT NULL,
    bag_status    VARCHAR(16) NULL,
    PRIMARY KEY (bag_id),
    CONSTRAINT fk_bag_passenger FOREIGN KEY (passenger_id) REFERENCES Passenger(passenger_id)
) ENGINE=InnoDB COMMENT='SYNTHETIC - checked bags';

CREATE TABLE KPIThreshold (
    kpi_id             INT          NOT NULL,
    metric_name        VARCHAR(40)  NOT NULL,
    threshold_minutes  INT          NOT NULL,
    target_pct         DECIMAL(5,2) NOT NULL,
    PRIMARY KEY (kpi_id),
    UNIQUE KEY uq_metric (metric_name)
) ENGINE=InnoDB COMMENT='The business rule - deliberately not linked to anything';
