ALTER TABLE entry_exit_study_history ADD COLUMN IF NOT EXISTS news_sentiment text;
ALTER TABLE entry_exit_study_history ADD COLUMN IF NOT EXISTS news_sentiment_reason text;
