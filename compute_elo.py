#!/usr/bin/env python3
"""Bradley-Terry / sequential Elo ratings from pairwise design battles."""

from __future__ import annotations

import argparse

import pandas as pd


def compute_elo(df: pd.DataFrame, k_factor: int = 32, base_rating: int = 1000) -> pd.DataFrame:
    ratings: dict[str, float] = {}
    wins: dict[str, float] = {}
    matches: dict[str, int] = {}

    models = set(df["model_a"]).union(set(df["model_b"]))
    for model in models:
        ratings[model] = base_rating
        wins[model] = 0
        matches[model] = 0

    for _, row in df.iterrows():
        a, b, winner = row["model_a"], row["model_b"], row["winner"]
        ra, rb = ratings[a], ratings[b]
        ea = 1 / (1 + 10 ** ((rb - ra) / 400))
        eb = 1 / (1 + 10 ** ((ra - rb) / 400))

        matches[a] += 1
        matches[b] += 1

        if winner == a:
            sa, sb = 1.0, 0.0
            wins[a] += 1
        elif winner == b:
            sa, sb = 0.0, 1.0
            wins[b] += 1
        else:
            sa, sb = 0.5, 0.5
            wins[a] += 0.5
            wins[b] += 0.5

        ratings[a] += k_factor * (sa - ea)
        ratings[b] += k_factor * (sb - eb)

    summary = []
    for model in models:
        win_rate = (wins[model] / matches[model]) * 100 if matches[model] > 0 else 0
        summary.append(
            {
                "model": model,
                "elo": round(ratings[model], 1),
                "win_rate": round(win_rate, 1),
                "battles": matches[model],
            }
        )

    return pd.DataFrame(summary).sort_values(by="elo", ascending=False).reset_index(drop=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/battles.csv", help="Path to battles CSV")
    args = parser.parse_args()

    df = pd.read_csv(args.input)
    leaderboard = compute_elo(df)
    print(leaderboard.to_string(index=False))
