download-historical-news:
	./download_historical_news.sh


dataset:
	uv run src/news_sentiment/dataset.py \
	    --input_news data/cryptopanic_news.csv \
		--dataset_name news-sentiment-dataset-live \
		--samples 10 \
		--teacher_model gpt-4.1-mini

tricky-example:
	uv run src/news_sentiment/dataset.py \
	    --input_news "Pau Labrta Bajo think Goldman Sachs will buy 1B BTC, and sell 1B ETH" \
		--dataset_name news-sentiment-dataset-live \
		--teacher_model gpt-4.1-mini

curate-dataset:
	uv run src/news_sentiment/curate_dataset.py \
	    --dataset_name news-sentiment-dataset-live

evaluate:
	uv run src/news_sentiment/evaluate.py \
	    --dataset_name news-sentiment-dataset-live \
		--model gpt-4.1-mini

evaluate_tricky_example:
	uv run src/news_sentiment/evaluate.py \
	    --dataset_name news-sentiment-dataset-live \
		--model gpt-4.1-mini \
		--dataset_item_id 01998607-b23f-7540-8017-61ec93f418e6
