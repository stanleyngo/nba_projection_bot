import { render, screen } from "@testing-library/react";
import ProjectionCard from "./ProjectionCard";
import type { Projection } from "../types";

test("renders mean, over/under labels and model for a single-stat line", () => {
  const p: Projection = {
    player_name: "Nikola Jokic",
    stat: "points",
    line: 25.5,
    result: {
      mean: 28.3,
      median: 28,
      model: "negative_binomial",
      prob_over: 0.64,
      prob_under: 0.36,
      prob_push: 0,
    },
  };
  render(<ProjectionCard projection={p} />);

  expect(screen.getByText("Nikola Jokic")).toBeInTheDocument();
  expect(screen.getByText("28.3")).toBeInTheDocument();
  expect(screen.getByText(/Negative Binomial/)).toBeInTheDocument();
  expect(screen.getByText(/OVER\s*64%/)).toBeInTheDocument();
  expect(screen.getByText(/UNDER\s*36%/)).toBeInTheDocument();
});

test("renders a voided state and no meter when the player is out", () => {
  const p: Projection = {
    player_name: "Joel Embiid",
    stat: "points",
    line: 30.5,
    result: { injury_status: "out", available: false },
  };
  render(<ProjectionCard projection={p} />);

  expect(screen.getByText(/no projection/i)).toBeInTheDocument();
  expect(screen.queryByRole("img")).toBeNull(); // the shot-meter has role="img"
});

test("renders PRA label and component means for a combo prop", () => {
  const p: Projection = {
    player_name: "Luka Doncic",
    stat: "points+rebounds+assists",
    line: 45.5,
    result: {
      mean: 46.1,
      median: 46,
      model: "poisson",
      prob_over: 0.55,
      prob_under: 0.45,
      prob_push: 0,
      components: { points: 27.1, rebounds: 8.4, assists: 9.0 },
    },
  };
  render(<ProjectionCard projection={p} />);

  expect(screen.getByText(/PRA/)).toBeInTheDocument();
  expect(screen.getByText(/PTS 27.1/)).toBeInTheDocument();
  expect(screen.getByText(/REB 8.4/)).toBeInTheDocument();
});
