from __future__ import annotations

import os

import numpy as np
import pandas as pd

from common import CFG, curated_path, flush_audit, qa_log

CURATED = CFG["paths"]["curated"]
P = CFG["passenger_layer"]

FIRST = ["James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael",
         "Linda", "David", "Elizabeth", "William", "Barbara", "Richard", "Susan",
         "Joseph", "Jessica", "Thomas", "Sarah", "Charles", "Karen", "Amara",
         "Wei", "Priya", "Mateo", "Fatima", "Yusuf", "Ana", "Dmitri", "Aiko", "Kofi"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
        "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
        "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
        "Okafor", "Chen", "Patel", "Silva", "Haddad", "Nguyen", "Kowalski",
        "Ivanov", "Tanaka", "Mensah"]
FARE_CLASSES = ["Economy", "Economy", "Economy", "Economy", "Economy",
                "PremiumEconomy", "Business", "First"]
BAG_STATUS = ["Delivered", "Delayed", "Damaged", "Lost"]

def main() -> None:
    print("=== STAGE 4b: SYNTHESIZE PASSENGER LAYER (clearly labelled) ===\n")
    rng = np.random.default_rng(P["seed"])

    flight = pd.read_parquet(os.path.join(CURATED, "entity_Flight.parquet"),
                             columns=["flight_id", "aircraft_id", "service_date",
                                      "is_analytical", "delay_minutes"])

    aircraft = None

    eligible = flight[flight["is_analytical"] & flight["aircraft_id"].notna()]
    n = min(int(P["flights_sampled"]), len(eligible))
    sample = eligible.sample(n=n, random_state=P["seed"]).copy()
    qa_log("synth", "eligible_real_flights", len(eligible))
    qa_log("synth", "flights_sampled", n,
           f"{100*n/len(eligible):.3f}% of analytical flights")

    seats = np.full(len(sample), float(P["default_seats"]))
    pax_per_flight = np.maximum(
        1, rng.binomial(seats.astype(int), P["load_factor"])
    )
    total_boardings = int(pax_per_flight.sum())
    qa_log("synth", "total_boardings", total_boardings)

    n_passengers = int(total_boardings * (1 - P["repeat_traveller_rate"]))
    pid = np.arange(1, n_passengers + 1)
    passenger = pd.DataFrame({
        "passenger_id": pid,
        "first_name": rng.choice(FIRST, n_passengers),
        "last_name": rng.choice(LAST, n_passengers),
    })
    qa_log("synth", "Passenger_rows", len(passenger))

    flight_ids = np.repeat(sample["flight_id"].to_numpy(), pax_per_flight)

    pax_ids = rng.integers(1, n_passengers + 1, total_boardings)

    row_seat = np.concatenate([np.arange(1, c + 1) for c in pax_per_flight])
    seat_letter = np.array(list("ABCDEF"))[(row_seat - 1) % 6]
    seat_row = ((row_seat - 1) // 6) + 1

    boarding = pd.DataFrame({
        "boarding_pass_id": np.arange(1, total_boardings + 1),
        "passenger_id": pax_ids,
        "flight_id": flight_ids,
        "seat_number": pd.Series(seat_row).astype(str) + pd.Series(seat_letter),
    })

    before = len(boarding)
    boarding = boarding.drop_duplicates(subset=["passenger_id", "flight_id"])
    qa_log("synth", "duplicate_passenger_flight_removed", before - len(boarding))
    qa_log("synth", "BoardingPass_rows", len(boarding))
    reuse = boarding.groupby("passenger_id").size()
    qa_log("synth", "passengers_with_multiple_boardings", int((reuse > 1).sum()),
           "this is what makes BoardingPass a real M:N bridge")

    has_bag = rng.random(len(boarding)) < P["baggage_rate_per_passenger"]
    bag_owners = boarding.loc[has_bag, ["passenger_id", "flight_id"]].reset_index(drop=True)
    n_bags = len(bag_owners)
    mishandled = rng.random(n_bags) < P["mishandled_baggage_rate"]
    status = np.where(mishandled,
                      rng.choice(BAG_STATUS[1:], n_bags, p=[0.72, 0.22, 0.06]),
                      "Delivered")
    baggage = pd.DataFrame({
        "bag_id": np.arange(1, n_bags + 1),
        "passenger_id": bag_owners["passenger_id"],
        "bag_status": status,
    })
    qa_log("synth", "Baggage_rows", len(baggage))
    qa_log("synth", "mishandled_bags", int(mishandled.sum()),
           f"{100*mishandled.mean():.3f}% - realistic order of magnitude")

    real_flights = set(flight["flight_id"])
    orphan_f = len(set(boarding["flight_id"]) - real_flights)
    orphan_p = len(set(boarding["passenger_id"]) - set(passenger["passenger_id"]))
    orphan_b = len(set(baggage["passenger_id"]) - set(passenger["passenger_id"]))
    qa_log("referential", "BoardingPass.flight_id_orphans", orphan_f, "must be 0")
    qa_log("referential", "BoardingPass.passenger_id_orphans", orphan_p, "must be 0")
    qa_log("referential", "Baggage.passenger_id_orphans", orphan_b, "must be 0")
    assert orphan_f == orphan_p == orphan_b == 0, "synthetic layer broke referential integrity"

    for name, df in [("Passenger", passenger), ("BoardingPass", boarding),
                     ("Baggage", baggage)]:
        path = curated_path(f"entity_{name}.parquet")
        df.to_parquet(path, index=False)
        print(f"  {name:<14} {len(df):>10,} rows -> {os.path.basename(path)}  [SYNTHETIC]")

    flush_audit("qa_stage4b_synthetic_passenger_layer")

if __name__ == "__main__":
    main()
