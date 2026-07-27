// Mirrors the FastAPI /ask response (api.py AskResponse) and the card records
// built in agent.py (projection_record / news_record).

export interface ProjectionResult {
  mean?: number;
  median?: number;
  model?: string;
  prob_over?: number;
  prob_under?: number;
  prob_push?: number;
  available?: boolean;
  injury_status?: string;
  injury_factor?: number;
  components?: Record<string, number>;
}

export interface Projection {
  player_name: string;
  stat: string;
  line: number | null;
  result: ProjectionResult;
}

export interface NewsItem {
  text: string;
  url: string;
  title: string;
}

export interface NewsContext {
  player_name: string;
  news: NewsItem[];
  analysis: NewsItem[];
}

export interface AskResponse {
  answer: string;
  conversation_id: number;
  projections: Projection[];
  news: NewsContext[];
}
