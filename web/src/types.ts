export type RandomClue = {
  id: number;
  jarchive_game_id: number;
  air_date: string;
  year: number | null;
  round: string;
  game_category: string;
  value_display: string | null;
  value_amount: number | null;
  is_daily_double: boolean;
  clue_text: string;
  answer_text: string;
};
