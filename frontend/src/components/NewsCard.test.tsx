import { render, screen } from "@testing-library/react";
import NewsCard from "./NewsCard";
import type { NewsContext } from "../types";

const ctx: NewsContext = {
  player_name: "LeBron James",
  news: [{ text: "Listed questionable with ankle soreness.", url: "https://a.com", title: "Injury report" }],
  analysis: [{ text: "Expects a bounce-back game.", url: "https://b.com", title: "Outlook" }],
};

test("renders news and analysis with sources, labeling analysis as opinion", () => {
  render(<NewsCard context={ctx} />);

  expect(screen.getByText("News & Analysis")).toBeInTheDocument();

  const newsLink = screen.getByRole("link", { name: "Injury report" });
  expect(newsLink).toHaveAttribute("href", "https://a.com");
  expect(newsLink).toHaveAttribute("target", "_blank");

  const analysisLink = screen.getByRole("link", { name: "Outlook" });
  expect(analysisLink).toHaveAttribute("href", "https://b.com");

  // Analysis must be explicitly marked as opinion.
  expect(screen.getByText("Opinion")).toBeInTheDocument();
  expect(screen.getByText(/opinion, not fact/)).toBeInTheDocument();
});

test("renders nothing when both lists are empty", () => {
  const { container } = render(
    <NewsCard context={{ player_name: "Nobody", news: [], analysis: [] }} />,
  );
  expect(container).toBeEmptyDOMElement();
});
