from typing import Optional

from opik import Opik
from opik.evaluation import evaluate

from news_sentiment.metrics import SameScoreMetric
from news_sentiment.sentiment_extractor import SentimentExtractor


def evaluate_sentiment_extractor(
    dataset_name: str, model: str, dataset_item_id: Optional[str] = None
):
    """
    Evaluates the sentiment extractor on the given dataset


    """
    # Load the dataset from opik
    client = Opik()
    dataset = client.get_or_create_dataset(name=dataset_name)

    # load the sentiment extractor solution we want to evaluate
    sentiment_extractor = SentimentExtractor(model=model)

    #
    def evaluation_task(x):
        return {
            'scores': sentiment_extractor.extract_sentiment_scores(x['input']).scores,
            'reason': sentiment_extractor.extract_sentiment_scores(x['input']).reason,
        }

    # Define evaluation metric
    same_btc_score_metric = SameScoreMetric(name='same_btc_score_metric', coin='BTC')
    same_eth_score_metric = SameScoreMetric(name='same_eth_score_metric', coin='ETH')

    # evaluate() returns a result object but Opik logs the experiment
    # server-side automatically, so we don't need to capture or return it.
    evaluate(
        dataset=dataset,
        task=evaluation_task,
        scoring_metrics=[
            same_btc_score_metric,
            same_eth_score_metric,
        ],
        experiment_config={'model': model},
        task_threads=1,
        dataset_item_ids=[dataset_item_id] if dataset_item_id else None,
    )


if __name__ == '__main__':
    from fire import Fire

    Fire(evaluate_sentiment_extractor)
