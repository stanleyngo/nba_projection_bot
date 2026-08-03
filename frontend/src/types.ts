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

// Mirrors GET /conversations (api.py ConversationSummary).
export interface ConversationSummary {
  id: number;
  title: string | null;
  created_at: string;
}

// Mirrors GET /conversations/{id} (api.py ConversationHistoryResponse).
// projections/news are only ever populated on assistant messages — empty
// on user messages and on assistant messages that surfaced no cards.
export interface ConversationHistoryMessage {
  role: "user" | "assistant";
  content: string;
  projections: Projection[];
  news: NewsContext[];
}

// Mirrors POST /deep-analysis (api.py DeepAnalysisJobRef)
export interface DeepAnalysisJobRef {
  job_id: number;
}

// Mirrors GET /deep-analysis/{job_id} (api.py DeepAnalysisJobResponse).
// `produced` is false only while `status` is "queued" and the job's Kafka
// message hasn't been confirmed delivered yet (e.g. the free-tier Kafka
// service is asleep) — a background retry loop keeps trying until it is.
export interface DeepAnalysisJobResponse {
  status: string;
  result: string | null;
  error: string | null;
  produced: boolean;
  created_at: string;
}

// Mirrors GET /deep-analysis (api.py DeepAnalysisJobSummary).
export interface DeepAnalysisJobSummary {
  id: number;
  player_name: string;
  status: string;
  produced: boolean;
  created_at: string;
}
