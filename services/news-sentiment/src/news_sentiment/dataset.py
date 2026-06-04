import os
from typing import Optional

import pandas as pd
from loguru import logger
from opik import Opik
from tqdm import tqdm

from news_sentiment.baml_client.types import SentimentScores
from news_sentiment.sentiment_extractor import SentimentExtractor


def load_news_from_csv(input_csv_file: str, samples: Optional[int] = None) -> list[str]:
    """
    Returns a list of samples from the given csv file
    """
    df = pd.read_csv(input_csv_file)

    if samples:
        df = df.sample(samples)

    return df['title'].tolist()


def generate(
    input_news: str,
    dataset_name: str,
    teacher_model: str,
    samples: Optional[int] = None,
):
    """
    Creates a dataset....


    """
    if input_news.endswith('.csv'):
        input_csv_file = input_news

        logger.info(f'Loading {samples} news from {input_csv_file}...')
        news: list[str] = load_news_from_csv(input_csv_file, samples)
        logger.info(f'Loaded {len(news)} news...')

    else:
        logger.info(f'Loading single news item from {input_news}...')
        news = [input_news]

    sentiment_extractor = SentimentExtractor(model=teacher_model)

    # Create a dataset
    client = Opik(api_key=os.getenv('OPIK_API_KEY'))
    dataset = client.get_or_create_dataset(name=dataset_name)

    for news_item in tqdm(news):
        output: SentimentScores = sentiment_extractor.extract_sentiment_scores(
            news_item
        )

        output_scores = [
            {
                'coin': score.coin,
                'score': score.score,
            }
            for score in output.scores
        ]

        row = {
            'input': news_item,
            'expected_output': output_scores,
            'expected_reason': output.reason,
            'teacher_model': teacher_model,
        }

        dataset.insert([row])


if __name__ == '__main__':
    from fire import Fire

    Fire(generate)
